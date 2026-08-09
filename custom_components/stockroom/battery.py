"""Battery level / type syncing between Home Assistant and Stockroom.

For every Stockroom item linked to a Home Assistant device or entity that has a
battery, push the level up to Stockroom on every change and at least once a day
(heartbeat), and mirror the battery type from the Battery Notes integration when
it is installed. Stockroom owns all forecasting; this only pushes raw readings.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)

from .api import (
    StockroomApiClient,
    StockroomApiError,
    StockroomConnectionError,
    StockroomPermissionError,
)
from .const import (
    ATTR_BATTERY_LEVEL,
    ATTR_BATTERY_QUANTITY,
    ATTR_BATTERY_TYPE,
    ATTR_BATTERY_TYPE_AND_QUANTITY,
    BATTERY_HEARTBEAT_HOURS,
    BATTERY_NOTES_DOMAIN,
    EVENT_BATTERY_NOTES_REPLACED,
)
from .coordinator import StockroomConfigEntry, StockroomDataUpdateCoordinator
from .linking import resolve_device_id, resolve_device_ids
from .models import BatteryLinkTarget

_LOGGER = logging.getLogger(__name__)

DEVICE_CLASS_BATTERY = "battery"
_IGNORED_STATES = (STATE_UNKNOWN, STATE_UNAVAILABLE)


@dataclass(slots=True, frozen=True)
class _BatterySource:
    """Where a linked item's battery percentage is read from in HA."""

    entity_id: str
    # None → the entity's state is the percentage; otherwise read this attribute.
    attribute: str | None


def _is_numeric(value: object) -> bool:
    """Return True if value can be parsed as a float."""
    try:
        float(value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return False
    return True


def _clamp_percent(value: float) -> int:
    """Round to an integer battery percentage clamped to 0-100."""
    return max(0, min(100, round(value)))


def _format_battery_type(battery_type: str, quantity: object) -> str:
    """Format a Battery Notes type + quantity as e.g. "AA x4" (single omits).

    The multiplication sign is intentional, matching Stockroom's convention.
    """
    if isinstance(quantity, (int, float)) and int(quantity) > 1:
        return f"{battery_type} ×{int(quantity)}"
    return battery_type


def _entity_entries_for_device(
    hass: HomeAssistant,
    registry: er.EntityRegistry,
    device_id: str,
    *,
    include_disabled_entities: bool = True,
) -> list[er.RegistryEntry]:
    """Return the entity entries of a device, across HA 2026.8 device splits.

    A stored device id may predate the 2026.8 split, in which case the entities it
    used to carry are spread over the split devices - notably the Battery Notes
    ones, which live on a different split than the source integration's.
    """
    return [
        entry
        for resolved_id in resolve_device_ids(hass, device_id)
        for entry in er.async_entries_for_device(
            registry, resolved_id, include_disabled_entities=include_disabled_entities
        )
    ]


def battery_notes_type_for_device(hass: HomeAssistant, device_id: str) -> str | None:
    """Return a device's Battery Notes type/quantity formatted, or None.

    Reads ``battery_type`` / ``battery_quantity`` from the Battery Notes entities
    on the device; returns e.g. ``AA ×4`` (single cell omits the count). None
    when Battery Notes isn't installed or has no type for the device.
    """
    registry = er.async_get(hass)
    for entry in _entity_entries_for_device(hass, registry, device_id):
        if entry.platform != BATTERY_NOTES_DOMAIN:
            continue
        state = hass.states.get(entry.entity_id)
        if state is None:
            continue
        battery_type = state.attributes.get(ATTR_BATTERY_TYPE)
        if (
            isinstance(battery_type, str)
            and battery_type
            and battery_type.lower() != "unknown"
        ):
            quantity = state.attributes.get(ATTR_BATTERY_QUANTITY)
            return _format_battery_type(battery_type, quantity)
    return None


class StockroomBatterySync:
    """Keep linked items' battery level (and type) in sync with Stockroom."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: StockroomConfigEntry,
        api: StockroomApiClient,
        coordinator: StockroomDataUpdateCoordinator,
    ) -> None:
        """Initialize the battery sync manager."""
        self.hass = hass
        self.entry = entry
        self.api = api
        self.coordinator = coordinator

        self._unsubs: list[Callable[[], None]] = []
        self._unsub_state: Callable[[], None] | None = None

        self._item_source: dict[int, _BatterySource] = {}
        self._entity_to_items: dict[str, set[int]] = {}
        self._entity_attr: dict[str, str | None] = {}
        self._device_to_items: dict[str, set[int]] = {}
        self._tracked_entities: set[str] = set()
        self._tracked_items: set[int] = set()
        self._type_cache: dict[int, str] = {}
        self._write_warned = False

    @callback
    def async_setup(self) -> None:
        """Register listeners and do an initial reconcile."""
        self._unsubs.append(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                self._async_heartbeat,
                timedelta(hours=BATTERY_HEARTBEAT_HOURS),
            )
        )
        self._unsubs.append(
            self.hass.bus.async_listen(
                EVENT_BATTERY_NOTES_REPLACED, self._async_handle_battery_replaced
            )
        )
        self._reconcile()

    @callback
    def async_shutdown(self) -> None:
        """Unsubscribe everything."""
        if self._unsub_state is not None:
            self._unsub_state()
            self._unsub_state = None
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    async def async_resync_now(self) -> None:
        """Force-push the current level and re-set the type for every target.

        Used by the manual "Repair links" action: it pushes the current battery
        level and re-applies the Battery Notes type for each linked item right
        now, instead of waiting for the next change or the daily heartbeat.
        """
        data = self.coordinator.data
        targets = list(data.battery_targets) if data is not None else []
        # Drop the cache so a type that drifted on the Stockroom side is re-set.
        self._type_cache.clear()
        for target in targets:
            source = self._resolve_source(target)
            if source is not None:
                state = self.hass.states.get(source.entity_id)
                percent = self._percent_from_state(state, source.attribute)
                if percent is not None:
                    await self._async_push(target.item_id, percent)
            await self._async_sync_battery_type(target)

    # -- Reconciliation --------------------------------------------------

    @callback
    def _handle_coordinator_update(self) -> None:
        """Re-resolve battery targets when the coordinator refreshes links."""
        self._reconcile()

    @callback
    def _reconcile(self) -> None:
        """Rebuild the tracked battery entities from the coordinator's targets."""
        data = self.coordinator.data
        targets = list(data.battery_targets) if data is not None else []

        item_source: dict[int, _BatterySource] = {}
        entity_to_items: dict[str, set[int]] = {}
        entity_attr: dict[str, str | None] = {}
        device_to_items: dict[str, set[int]] = {}

        for target in targets:
            if (device_id := self._device_id_for_target(target)) is not None:
                device_to_items.setdefault(device_id, set()).add(target.item_id)
            source = self._resolve_source(target)
            if source is None:
                continue
            item_source[target.item_id] = source
            entity_to_items.setdefault(source.entity_id, set()).add(target.item_id)
            entity_attr[source.entity_id] = source.attribute

        new_items = set(item_source) - self._tracked_items

        self._item_source = item_source
        self._entity_to_items = entity_to_items
        self._entity_attr = entity_attr
        self._device_to_items = device_to_items
        self._tracked_items = set(item_source)

        self._resubscribe(set(entity_to_items))

        # Push an anchor reading for newly tracked items and sync battery types.
        self.entry.async_create_background_task(
            self.hass,
            self._async_after_reconcile(targets, new_items),
            name="stockroom_battery_reconcile",
        )

    @callback
    def _resubscribe(self, entity_ids: set[str]) -> None:
        """Re-point the state-change listener if the tracked set changed."""
        if entity_ids == self._tracked_entities:
            return
        if self._unsub_state is not None:
            self._unsub_state()
            self._unsub_state = None
        if entity_ids:
            self._unsub_state = async_track_state_change_event(
                self.hass, list(entity_ids), self._handle_state_event
            )
        self._tracked_entities = entity_ids

    async def _async_after_reconcile(
        self, targets: list[BatteryLinkTarget], new_items: set[int]
    ) -> None:
        """Push initial readings for new items and reconcile battery types."""
        for item_id in new_items:
            source = self._item_source.get(item_id)
            if source is None:
                continue
            state = self.hass.states.get(source.entity_id)
            percent = self._percent_from_state(state, source.attribute)
            if percent is not None:
                recorded_at = (
                    state.last_changed.isoformat()
                    if state is not None and state.last_changed
                    else None
                )
                await self._async_push(item_id, percent, recorded_at)
        for target in targets:
            await self._async_sync_battery_type(target)

    # -- Battery level pushing -------------------------------------------

    @callback
    def _handle_state_event(self, event: Event) -> None:
        """Push a reading whenever a tracked battery entity changes."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        entity_id = new_state.entity_id
        percent = self._percent_from_state(new_state, self._entity_attr.get(entity_id))
        if percent is None:
            return
        recorded_at = (
            new_state.last_changed.isoformat() if new_state.last_changed else None
        )
        for item_id in self._entity_to_items.get(entity_id, set()):
            self.entry.async_create_background_task(
                self.hass,
                self._async_push(item_id, percent, recorded_at),
                name=f"stockroom_battery_push_{item_id}",
            )

    async def _async_heartbeat(self, _now: datetime) -> None:
        """Push the current level of every tracked battery once a day."""
        for item_id, source in list(self._item_source.items()):
            state = self.hass.states.get(source.entity_id)
            percent = self._percent_from_state(state, source.attribute)
            if percent is not None:
                await self._async_push(item_id, percent)

    async def _async_push(
        self, item_id: int, percent: int, recorded_at: str | None = None
    ) -> None:
        """POST a battery reading, swallowing transient/permission errors."""
        try:
            await self.api.async_post_battery_reading(item_id, percent, recorded_at)
        except StockroomPermissionError:
            self._warn_write_once()
        except StockroomConnectionError:
            _LOGGER.debug(
                "Stockroom unreachable while pushing battery reading for item %s",
                item_id,
            )
        except StockroomApiError as err:
            _LOGGER.warning(
                "Failed to push battery reading for Stockroom item %s: %s",
                item_id,
                err,
            )

    # -- Battery type syncing (Battery Notes) ----------------------------

    async def _async_sync_battery_type(self, target: BatteryLinkTarget) -> None:
        """PATCH the item's battery type from Battery Notes if it changed."""
        device_id = self._device_id_for_target(target)
        if device_id is None:
            return
        formatted = self._battery_notes_type(device_id)
        if formatted is None:
            return
        if formatted == target.battery_type or formatted == self._type_cache.get(
            target.item_id
        ):
            return
        try:
            await self.api.async_set_battery_type(target.item_id, formatted)
            self._type_cache[target.item_id] = formatted
        except StockroomPermissionError:
            self._warn_write_once()
        except StockroomConnectionError:
            _LOGGER.debug(
                "Stockroom unreachable while setting battery type for item %s",
                target.item_id,
            )
        except StockroomApiError as err:
            _LOGGER.warning(
                "Failed to set battery type for Stockroom item %s: %s",
                target.item_id,
                err,
            )

    def _battery_notes_type(self, device_id: str) -> str | None:
        """Read battery type/quantity from Battery Notes for a device, if any."""
        return battery_notes_type_for_device(self.hass, device_id)

    # -- Battery change events -------------------------------------------

    async def _async_handle_battery_replaced(self, event: Event) -> None:
        """Record a battery change when Battery Notes reports a replacement."""
        device_id = event.data.get("device_id")
        if not isinstance(device_id, str):
            return
        item_ids = self._device_to_items.get(device_id)
        if not item_ids:
            return
        type_and_quantity = event.data.get(ATTR_BATTERY_TYPE_AND_QUANTITY)
        notes = (
            f"Battery replaced ({type_and_quantity})."
            if isinstance(type_and_quantity, str) and type_and_quantity
            else "Battery replaced."
        )
        for item_id in item_ids:
            try:
                await self.api.async_post_battery_change(item_id, notes=notes)
            except StockroomPermissionError:
                self._warn_write_once()
            except StockroomConnectionError:
                _LOGGER.debug(
                    "Stockroom unreachable while recording battery change for item %s",
                    item_id,
                )
            except StockroomApiError as err:
                _LOGGER.warning(
                    "Failed to record battery change for Stockroom item %s: %s",
                    item_id,
                    err,
                )

    # -- Resolution helpers ----------------------------------------------

    def _resolve_source(self, target: BatteryLinkTarget) -> _BatterySource | None:
        """Find the HA battery percentage source for a linked item."""
        registry = er.async_get(self.hass)
        if target.ha_device_id is not None:
            # The server stores the device id, which may predate HA 2026.8's split.
            entries = _entity_entries_for_device(
                self.hass,
                registry,
                target.ha_device_id,
                include_disabled_entities=False,
            )
            if entries:
                for entry in sorted(entries, key=lambda e: e.entity_id):
                    state = self.hass.states.get(entry.entity_id)
                    if (
                        state is not None
                        and state.attributes.get(ATTR_DEVICE_CLASS)
                        == DEVICE_CLASS_BATTERY
                        and _is_numeric(state.state)
                    ):
                        return _BatterySource(entry.entity_id, None)

        if target.ha_entity_id is not None:
            state = self.hass.states.get(target.ha_entity_id)
            if state is not None:
                if state.attributes.get(
                    ATTR_DEVICE_CLASS
                ) == DEVICE_CLASS_BATTERY and _is_numeric(state.state):
                    return _BatterySource(target.ha_entity_id, None)
                if isinstance(state.attributes.get(ATTR_BATTERY_LEVEL), (int, float)):
                    return _BatterySource(target.ha_entity_id, ATTR_BATTERY_LEVEL)
        return None

    def _device_id_for_target(self, target: BatteryLinkTarget) -> str | None:
        """Resolve a target's HA device id (directly or via its entity)."""
        if target.ha_device_id is not None:
            return resolve_device_id(self.hass, target.ha_device_id)
        if target.ha_entity_id is not None:
            entry = er.async_get(self.hass).async_get(target.ha_entity_id)
            return entry.device_id if entry is not None else None
        return None

    def _percent_from_state(self, state: object, attribute: str | None) -> int | None:
        """Extract a clamped integer percentage from a state, or None."""
        if state is None:
            return None
        if attribute is None:
            if state.state in _IGNORED_STATES:  # type: ignore[attr-defined]
                return None
            raw: object = state.state  # type: ignore[attr-defined]
        else:
            raw = state.attributes.get(attribute)  # type: ignore[attr-defined]
        if not _is_numeric(raw):
            return None
        return _clamp_percent(float(raw))  # type: ignore[arg-type]

    @callback
    def _warn_write_once(self) -> None:
        """Warn once that the configured token cannot write battery data."""
        if self._write_warned:
            return
        self._write_warned = True
        _LOGGER.warning(
            "Stockroom token lacks the write ability; battery data cannot be "
            "pushed. Re-authenticate with a token that has the write ability"
        )
