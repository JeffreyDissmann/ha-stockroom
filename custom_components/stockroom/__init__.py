"""The Stockroom integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_TOKEN, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import StockroomApiClient, StockroomApiError
from .battery import StockroomBatterySync
from .const import (
    CONF_HA_DEVICE_TO_ITEM,
    CONF_ITEM_TO_HA_DEVICE,
    CONF_LINKS,
    DOMAIN,
)
from .coordinator import StockroomConfigEntry, StockroomDataUpdateCoordinator
from .linking import (
    async_cleanup_removed_ha_device_link,
    async_refresh_link,
    get_link_maps,
)
from .services import async_setup_services, async_unload_services

PLATFORMS: list[Platform] = [Platform.SENSOR]
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: StockroomConfigEntry) -> bool:
    """Set up Stockroom from a config entry."""
    if CONF_LINKS not in entry.options:
        hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                CONF_LINKS: {
                    CONF_HA_DEVICE_TO_ITEM: {},
                    CONF_ITEM_TO_HA_DEVICE: {},
                },
            },
        )

    api = StockroomApiClient(
        entry.data[CONF_HOST],
        entry.data[CONF_TOKEN],
        async_get_clientsession(hass),
    )
    coordinator = StockroomDataUpdateCoordinator(hass, api, entry)
    await coordinator.async_config_entry_first_refresh()

    @callback
    def _async_handle_device_registry_updated(
        event: Event[dr.EventDeviceRegistryUpdatedData],
    ) -> None:
        """React to a linked HA device being renamed or removed."""
        action = event.data["action"]
        device_id = event.data["device_id"]

        if action == "update":
            changes = event.data.get("changes", {})
            if "name_by_user" not in changes and "name" not in changes:
                return
            ha_device_to_item, _ = get_link_maps(entry)
            if device_id not in ha_device_to_item:
                return

            async def _async_sync_friendly_name() -> None:
                current = hass.config_entries.async_get_entry(entry.entry_id)
                if current is None or current.state is not ConfigEntryState.LOADED:
                    return
                try:
                    await async_refresh_link(hass, entry, api, device_id)
                except StockroomApiError:
                    _LOGGER.warning(
                        "Unable to update Stockroom link name after device rename"
                    )

            hass.async_create_task(_async_sync_friendly_name())
            return

        if action != "remove":
            return

        async def _async_cleanup_removed_device() -> None:
            current_entry = hass.config_entries.async_get_entry(entry.entry_id)
            if (
                current_entry is None
                or current_entry.state is not ConfigEntryState.LOADED
            ):
                return
            try:
                new_options = await async_cleanup_removed_ha_device_link(
                    hass, entry, api, device_id
                )
            except StockroomApiError:
                _LOGGER.warning(
                    "Unable to clean up Stockroom link after HA device removal"
                )
                return
            if new_options is not None:
                refreshed = hass.config_entries.async_get_entry(entry.entry_id)
                if refreshed is not None and refreshed.state is ConfigEntryState.LOADED:
                    hass.config_entries.async_update_entry(
                        refreshed, options=new_options
                    )

        hass.async_create_task(_async_cleanup_removed_device())

    entry.async_on_unload(
        hass.bus.async_listen(
            dr.EVENT_DEVICE_REGISTRY_UPDATED, _async_handle_device_registry_updated
        )
    )

    async_setup_services(hass)

    battery_sync = StockroomBatterySync(hass, entry, api, coordinator)
    coordinator.battery_sync = battery_sync
    battery_sync.async_setup()
    entry.async_on_unload(battery_sync.async_shutdown)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: StockroomConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not any(
        other.entry_id != entry.entry_id and other.state is ConfigEntryState.LOADED
        for other in hass.config_entries.async_entries(DOMAIN)
    ):
        # Services are domain-global; only remove them with no other entry loaded.
        async_unload_services(hass)
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: StockroomConfigEntry) -> None:
    """Reload the entry after options or links change."""
    await hass.config_entries.async_reload(entry.entry_id)
