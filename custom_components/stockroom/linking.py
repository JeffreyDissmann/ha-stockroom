"""Device <-> item linking helpers for Stockroom."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    instance_id,
)
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .api import StockroomApiClient, StockroomApiError
from .const import (
    CONF_AREA_LINKS,
    CONF_HA_DEVICE_TO_ITEM,
    CONF_ITEM_TO_HA_DEVICE,
    CONF_LINKS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def get_area_room_map(
    config_entry: ConfigEntry,
    options: Mapping[str, Any] | None = None,
) -> dict[str, int]:
    """Return the normalized HA-area -> Stockroom-room id map from options."""
    source_options = options if options is not None else config_entry.options
    raw = source_options.get(CONF_AREA_LINKS, {})
    area_room: dict[str, int] = {}
    if isinstance(raw, dict):
        for area_id, room_id in raw.items():
            try:
                area_room[str(area_id)] = int(room_id)
            except TypeError, ValueError:
                continue
    return area_room


def build_updated_area_options(
    config_entry: ConfigEntry,
    area_room: dict[str, int],
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a new options dict carrying the given area -> room map."""
    source_options = options if options is not None else config_entry.options
    return {
        **source_options,
        CONF_AREA_LINKS: {
            area_id: int(room_id) for area_id, room_id in area_room.items()
        },
    }


def get_link_maps(
    config_entry: ConfigEntry,
    options: Mapping[str, Any] | None = None,
) -> tuple[dict[str, int], dict[int, str]]:
    """Return normalized HA-device <-> Stockroom-item link maps."""
    source_options = options if options is not None else config_entry.options
    links = source_options.get(CONF_LINKS, {})
    raw_device_to_item = links.get(CONF_HA_DEVICE_TO_ITEM, {})
    raw_item_to_device = links.get(CONF_ITEM_TO_HA_DEVICE, {})

    ha_device_to_item: dict[str, int] = {}
    if isinstance(raw_device_to_item, dict):
        for device_id, item_id in raw_device_to_item.items():
            try:
                ha_device_to_item[str(device_id)] = int(item_id)
            except TypeError, ValueError:
                continue

    item_to_ha_device: dict[int, str] = {}
    if isinstance(raw_item_to_device, dict):
        for item_id, device_id in raw_item_to_device.items():
            try:
                item_to_ha_device[int(item_id)] = str(device_id)
            except TypeError, ValueError:
                continue

    return ha_device_to_item, item_to_ha_device


def build_updated_options(
    config_entry: ConfigEntry,
    ha_device_to_item: dict[str, int],
    item_to_ha_device: dict[int, str],
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a new options dict carrying the given link maps."""
    source_options = options if options is not None else config_entry.options
    return {
        **source_options,
        CONF_LINKS: {
            CONF_HA_DEVICE_TO_ITEM: dict(ha_device_to_item),
            CONF_ITEM_TO_HA_DEVICE: {
                str(item_id): device_id
                for item_id, device_id in item_to_ha_device.items()
            },
        },
    }


@callback
def resolve_device_ids(hass: HomeAssistant, device_id: str) -> list[str]:
    """Return every live device id a stored device id stands for.

    Home Assistant 2026.8 restricted a device to a single config entry and split
    devices that belonged to several entries into one device per entry. The old
    ("composite") id is only resolved by a read-only shim that is not part of the
    registry, and the entities of the merged device were distributed over the
    splits - so anything enumerating a stored device's entities has to look at all
    of them. Returns ``[device_id]`` for a live device and ``[]`` for an unknown id.
    """
    registry = dr.async_get(hass)
    is_composite = registry.async_is_composite_device_id(device_id)
    if is_composite is False:
        return [device_id]
    if is_composite is None:
        return []
    return [
        split.id
        for split in registry.async_get_devices_for_composite_device_id(device_id)
    ]


@callback
def resolve_device_id(hass: HomeAssistant, device_id: str) -> str | None:
    """Map a stored device id onto the single device id to store and attach to.

    For a pre-migration composite this is the split that inherited the composite's
    former primary config entry - the device of the integration that actually owns
    the hardware - matching how core itself picks the base for its shim. Returns
    ``None`` for an unknown id. See :func:`resolve_device_ids` when enumerating
    entities, which have to be collected from every split.
    """
    registry = dr.async_get(hass)
    is_composite = registry.async_is_composite_device_id(device_id)
    if is_composite is False:
        return device_id
    if is_composite is None:
        return None

    splits = registry.async_get_devices_for_composite_device_id(device_id)
    if not splits:
        return None
    primary = splits[0].composite_primary_config_entry
    for split in splits:
        if split.config_entry_id == primary:
            return split.id
    return splits[0].id


def get_ha_device_url(hass: HomeAssistant, ha_device_id: str) -> str:
    """Build a Home Assistant deep link to a device's configuration page."""
    try:
        base_url = get_url(
            hass,
            prefer_external=True,
            allow_external=True,
            allow_internal=False,
        )
    except NoURLAvailableError:
        try:
            base_url = get_url(hass)
        except NoURLAvailableError:
            return f"/config/devices/device/{ha_device_id}"
    return f"{base_url.rstrip('/')}/config/devices/device/{ha_device_id}"


async def apply_link(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    api: StockroomApiClient,
    *,
    ha_entity_id: str,
    ha_device_id: str,
    item_id: int,
    friendly_name: str,
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the 1:1 link, write the Stockroom back-link, return new options.

    Pass ``options`` to build on an in-progress options dict (e.g. when linking
    several devices in a row before persisting), instead of the stored options.
    """
    ha_device_to_item, item_to_ha_device = get_link_maps(config_entry, options)

    if (existing := ha_device_to_item.get(ha_device_id)) is not None and (
        existing != item_id
    ):
        raise ValueError(
            f"Home Assistant device {ha_device_id} is already linked to item {existing}"
        )
    if (other := item_to_ha_device.get(item_id)) is not None and (
        other != ha_device_id
    ):
        raise ValueError(
            f"Stockroom item {item_id} is already linked to device {other}"
        )

    ha_device_url = get_ha_device_url(hass, ha_device_id)
    ha_instance_id = await instance_id.async_get(hass)
    await api.async_set_item_ha_link(
        item_id,
        ha_entity_id=ha_entity_id,
        ha_device_id=ha_device_id,
        friendly_name=friendly_name,
        url=ha_device_url,
        instance_id=ha_instance_id,
    )

    ha_device_to_item[ha_device_id] = item_id
    item_to_ha_device[item_id] = ha_device_id

    device_registry = dr.async_get(hass)
    if (device := device_registry.async_get(ha_device_id)) is not None and (
        not device.configuration_url
    ):
        device_registry.async_update_device(
            ha_device_id, configuration_url=api.get_item_url(item_id)
        )

    return build_updated_options(
        config_entry, ha_device_to_item, item_to_ha_device, options
    )


async def remove_link(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    api: StockroomApiClient,
    ha_device_id: str,
) -> dict[str, Any]:
    """Remove the link for a device, clearing the Stockroom back-link."""
    ha_device_to_item, item_to_ha_device = get_link_maps(config_entry)
    item_id = ha_device_to_item.pop(ha_device_id, None)
    if item_id is None:
        raise ValueError(f"Home Assistant device {ha_device_id} is not linked")
    item_to_ha_device.pop(item_id, None)

    await api.async_delete_item_ha_link(item_id)
    _clear_configuration_url_if_matching(hass, ha_device_id, api, item_id)

    return build_updated_options(config_entry, ha_device_to_item, item_to_ha_device)


async def async_cleanup_removed_ha_device_link(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    api: StockroomApiClient,
    ha_device_id: str,
) -> dict[str, Any] | None:
    """Remove a link when the Home Assistant device has been deleted."""
    ha_device_to_item, _ = get_link_maps(config_entry)
    if ha_device_id not in ha_device_to_item:
        return None
    return await remove_link(hass, config_entry, api, ha_device_id)


def _clear_configuration_url_if_matching(
    hass: HomeAssistant,
    ha_device_id: str,
    api: StockroomApiClient,
    item_id: int,
) -> None:
    """Clear a device's configuration URL if it points at the linked item."""
    device_registry = dr.async_get(hass)
    if (device := device_registry.async_get(ha_device_id)) is None:
        return
    item_url = api.get_item_url(item_id).rstrip("/")
    device_url = (device.configuration_url or "").rstrip("/")
    if device_url == item_url:
        device_registry.async_update_device(ha_device_id, configuration_url=None)


def primary_entity_id(hass: HomeAssistant, device_id: str) -> str | None:
    """Return a representative entity id for a device, preferring a primary one."""
    registry = er.async_get(hass)
    entries = [
        entry
        for resolved_id in resolve_device_ids(hass, device_id)
        for entry in er.async_entries_for_device(
            registry, resolved_id, include_disabled_entities=True
        )
    ]
    if not entries:
        return None
    for entry in entries:
        if not entry.disabled and entry.entity_category is None:
            return entry.entity_id
    return entries[0].entity_id


def device_friendly_name(hass: HomeAssistant, device_id: str) -> str:
    """Return a human-friendly name for a device."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return device_id
    return device.name_by_user or device.name or device_id


def linked_item_unique_id(config_entry_id: str, ha_device_id: str) -> str:
    """Return the unique id of the diagnostic sensor for a linked device."""
    return f"{config_entry_id}_{ha_device_id}_linked_item"


@callback
def _migrate_linked_item_unique_ids(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    remapped: Mapping[str, str],
) -> None:
    """Carry the diagnostic sensors over to the re-pointed device ids.

    The sensors' unique ids embed the linked device id, so without this the
    re-pointed links would register a second set of entities and orphan the
    originals instead of keeping their entity ids and history.
    """
    registry = er.async_get(hass)
    for old_device_id, new_device_id in remapped.items():
        old_unique_id = linked_item_unique_id(config_entry.entry_id, old_device_id)
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, old_unique_id)
        if entity_id is None:
            continue
        new_unique_id = linked_item_unique_id(config_entry.entry_id, new_device_id)
        if registry.async_get_entity_id("sensor", DOMAIN, new_unique_id) is not None:
            # Already migrated (or a leftover); leave both alone rather than collide.
            continue
        registry.async_update_entity(entity_id, new_unique_id=new_unique_id)


@callback
def async_remove_orphaned_split_devices(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> None:
    """Drop the empty device copies HA 2026.8's split handed this integration.

    Before 2026.8 the diagnostic sensor joined the linked device by copying its
    identifiers, which added this config entry to that device. The split therefore
    gave us a copy of every linked device; now that the sensors attach to the
    source device instead, ours are left behind empty and would show up as ghost
    duplicates. Call this after the platforms are set up, so the sensors have been
    re-attached and the leftovers really are empty.
    """
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    for device in dr.async_entries_for_config_entry(
        device_registry, config_entry.entry_id
    ):
        # Only devices this integration inherited from a split, never its own hub.
        if device.composite_device_id is None:
            continue
        if er.async_entries_for_device(
            entity_registry, device.id, include_disabled_entities=True
        ):
            continue
        _LOGGER.debug("Removing device %s left over from the 2026.8 split", device.id)
        device_registry.async_remove_device(device.id)


async def async_migrate_split_device_links(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    api: StockroomApiClient,
) -> dict[str, Any] | None:
    """Re-point stored links onto the devices HA 2026.8 split them into.

    Returns the updated options, or ``None`` when nothing needed re-pointing.
    Unknown device ids are left untouched - the owning integration may simply not
    have set its devices up yet, and a genuinely removed device is handled by the
    device-registry remove listener.
    """
    ha_device_to_item, item_to_ha_device = get_link_maps(config_entry)
    if not ha_device_to_item:
        return None

    remapped: dict[str, str] = {}
    for device_id in ha_device_to_item:
        resolved = resolve_device_id(hass, device_id)
        if resolved is not None and resolved != device_id:
            remapped[device_id] = resolved
    if not remapped:
        return None

    new_device_to_item = {
        remapped.get(device_id, device_id): item_id
        for device_id, item_id in ha_device_to_item.items()
    }
    new_item_to_device = {
        item_id: remapped.get(device_id, device_id)
        for item_id, device_id in item_to_ha_device.items()
    }

    _LOGGER.info(
        "Re-pointed %s Stockroom device link(s) onto the devices Home Assistant "
        "2026.8 split them into",
        len(remapped),
    )

    _migrate_linked_item_unique_ids(hass, config_entry, remapped)

    # Push the new device ids (and their deep links) back to Stockroom. A failure
    # here only leaves the server side stale; the "Repair links" action fixes it.
    for device_id in remapped.values():
        item_id = new_device_to_item.get(device_id)
        if item_id is None:
            continue
        try:
            await async_refresh_link(hass, config_entry, api, device_id, item_id)
        except StockroomApiError as err:
            _LOGGER.warning(
                "Unable to update the Stockroom link for item %s after re-pointing "
                "its Home Assistant device (%s); use the Repair links action to retry",
                item_id,
                err,
            )

    return build_updated_options(config_entry, new_device_to_item, new_item_to_device)


async def async_refresh_link(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    api: StockroomApiClient,
    device_id: str,
    item_id: int | None = None,
) -> None:
    """Re-push a device's Home Assistant link (refresh url / entity / name).

    Used to keep the Stockroom link current when a device is renamed and by the
    repair flow. No-op if the device is not linked or has no usable entity.
    """
    if item_id is None:
        ha_device_to_item, _ = get_link_maps(config_entry)
        item_id = ha_device_to_item.get(device_id)
    if item_id is None:
        return
    entity_id = primary_entity_id(hass, device_id)
    if entity_id is None:
        return
    await api.async_set_item_ha_link(
        item_id,
        ha_entity_id=entity_id,
        ha_device_id=device_id,
        friendly_name=device_friendly_name(hass, device_id),
        url=get_ha_device_url(hass, device_id),
        instance_id=await instance_id.async_get(hass),
    )
