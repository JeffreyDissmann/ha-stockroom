"""Data update coordinator for Stockroom."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
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
from .models import LinkedItem, StockroomStatistics

_LOGGER = logging.getLogger(__name__)

type StockroomConfigEntry = ConfigEntry[StockroomDataUpdateCoordinator]


@dataclass(slots=True, frozen=True)
class StockroomData:
    """Coordinator payload consumed by Stockroom entities."""

    statistics: StockroomStatistics
    linked_items: dict[str, LinkedItem]


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

    async def _async_update_data(self) -> StockroomData:
        """Fetch statistics and refresh linked-item state."""
        try:
            statistics = await self.api.async_get_statistics()
            linked_items = await self._async_fetch_linked_items()
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
        return StockroomData(statistics=statistics, linked_items=linked_items)

    async def _async_fetch_linked_items(self) -> dict[str, LinkedItem]:
        """Resolve the configured links to their current Stockroom item state.

        Uses a single ``GET /home-assistant-links`` call (rather than one
        ``GET /items/{id}`` per device) to avoid N round-trips and rate limits.
        """
        ha_device_to_item, _ = get_link_maps(self.config_entry)
        if not ha_device_to_item:
            return {}

        by_device: dict[str, dict] = {}
        for element in await self.api.async_get_ha_links():
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
