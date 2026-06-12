"""Data update coordinator for Stockroom."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import instance_id
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    StockroomApiClient,
    StockroomApiError,
    StockroomAuthenticationError,
    StockroomConnectionError,
    StockroomPermissionError,
    StockroomRateLimitError,
)
from .const import (
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
)
from .linking import get_link_maps
from .models import BatteryLinkTarget, LinkedItem, StockroomStatistics

_LOGGER = logging.getLogger(__name__)

type StockroomConfigEntry = ConfigEntry[StockroomDataUpdateCoordinator]


@dataclass(slots=True, frozen=True)
class StockroomData:
    """Coordinator payload consumed by Stockroom entities."""

    statistics: StockroomStatistics
    linked_items: dict[str, LinkedItem]
    battery_targets: list[BatteryLinkTarget]


class StockroomDataUpdateCoordinator(DataUpdateCoordinator[StockroomData]):
    """Manage fetching Stockroom statistics and linked-item state."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: StockroomApiClient,
        entry: StockroomConfigEntry,
    ) -> None:
        """Initialize the Stockroom coordinator."""
        try:
            minutes = int(
                entry.options.get(
                    CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
                )
            )
        except (TypeError, ValueError):
            minutes = DEFAULT_SCAN_INTERVAL_MINUTES
        minutes = max(
            MIN_SCAN_INTERVAL_MINUTES, min(MAX_SCAN_INTERVAL_MINUTES, minutes)
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=minutes),
        )
        self.api = api
        self._instance_id: str | None = None

    async def _async_update_data(self) -> StockroomData:
        """Fetch statistics and refresh linked-item / battery state."""
        try:
            if self._instance_id is None:
                self._instance_id = await instance_id.async_get(self.hass)
            statistics = await self.api.async_get_statistics()
            ha_links = await self.api.async_get_ha_links()
        except StockroomAuthenticationError as err:
            raise ConfigEntryAuthFailed("Stockroom token is invalid") from err
        except StockroomPermissionError as err:
            raise ConfigEntryAuthFailed(
                f"Stockroom token lacks a required ability: {err}"
            ) from err
        except StockroomRateLimitError as err:
            raise UpdateFailed("Stockroom rate limit exceeded") from err
        except StockroomConnectionError as err:
            raise UpdateFailed("Error communicating with Stockroom API") from err
        except StockroomApiError as err:
            raise UpdateFailed(f"Unexpected Stockroom API response: {err}") from err
        return StockroomData(
            statistics=statistics,
            linked_items=self._build_linked_items(ha_links),
            battery_targets=self._build_battery_targets(ha_links),
        )

    def _build_linked_items(
        self, ha_links: list[dict[str, Any]]
    ) -> dict[str, LinkedItem]:
        """Resolve the configured device links to their current item state.

        Driven by the integration's own option-stored device links (the links it
        owns); the embedded ``GET /home-assistant-links`` payload supplies each
        item's current name/quantity in a single call (no per-item N+1).
        """
        ha_device_to_item, _ = get_link_maps(self.config_entry)
        if not ha_device_to_item:
            return {}

        by_device: dict[str, dict] = {}
        for element in ha_links:
            link = element.get("home_assistant_link")
            if isinstance(link, dict) and isinstance(link.get("ha_device_id"), str):
                by_device[link["ha_device_id"]] = element

        linked_items: dict[str, LinkedItem] = {}
        for ha_device_id, item_id in ha_device_to_item.items():
            element = by_device.get(ha_device_id)
            if element is None:
                continue
            quantity = element.get("quantity")
            linked_items[ha_device_id] = LinkedItem(
                item_id=item_id,
                name=str(element.get("name") or f"Item {item_id}"),
                location_path=str(element.get("location_path") or ""),
                quantity=quantity if isinstance(quantity, int) else 1,
                url=self.api.get_item_url(item_id),
            )
        return linked_items

    def _build_battery_targets(
        self, ha_links: list[dict[str, Any]]
    ) -> list[BatteryLinkTarget]:
        """Parse the server links into battery-sync targets for this instance.

        The server is the source of truth for which items are linked; each link
        is scoped to a Home Assistant instance via ``instance_id`` (``None`` =
        unscoped/legacy, always honored).
        """
        targets: list[BatteryLinkTarget] = []
        for element in ha_links:
            if not isinstance(element, dict):
                continue
            item_id = element.get("id")
            link = element.get("home_assistant_link")
            if not isinstance(item_id, int) or not isinstance(link, dict):
                continue
            link_instance = link.get("instance_id")
            if link_instance is not None and link_instance != self._instance_id:
                continue
            device_id = link.get("ha_device_id")
            entity_id = link.get("ha_entity_id")
            device_id = device_id if isinstance(device_id, str) and device_id else None
            entity_id = entity_id if isinstance(entity_id, str) and entity_id else None
            if device_id is None and entity_id is None:
                continue
            battery_type = element.get("battery_type")
            targets.append(
                BatteryLinkTarget(
                    item_id=item_id,
                    ha_device_id=device_id,
                    ha_entity_id=entity_id,
                    instance_id=link_instance
                    if isinstance(link_instance, str)
                    else None,
                    battery_type=battery_type
                    if isinstance(battery_type, str) and battery_type
                    else None,
                )
            )
        return targets
