"""Service actions for Stockroom."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
import voluptuous as vol

from .api import (
    StockroomApiError,
    StockroomAuthenticationError,
    StockroomConnectionError,
    StockroomPermissionError,
    StockroomValidationError,
)
from .const import (
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    ATTR_ITEM_ID,
    ATTR_NAME,
    ATTR_PARENT_ID,
    ATTR_QUERY,
    ATTR_TYPE,
    DEFAULT_ITEM_TYPE,
    DOMAIN,
    ITEM_TYPES,
    SERVICE_CREATE_AND_LINK_ITEM,
    SERVICE_LINK_ITEM,
    SERVICE_SEARCH,
    SERVICE_UNLINK_ITEM,
)
from .coordinator import StockroomConfigEntry
from .linking import apply_link, remove_link

_LOGGER = logging.getLogger(__name__)

LINK_ITEM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_ITEM_ID): vol.Coerce(int),
        vol.Optional(ATTR_FRIENDLY_NAME): cv.string,
    }
)

UNLINK_ITEM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
    }
)

CREATE_AND_LINK_ITEM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_NAME): cv.string,
        vol.Optional(ATTR_TYPE, default=DEFAULT_ITEM_TYPE): vol.In(ITEM_TYPES),
        vol.Optional(ATTR_PARENT_ID): vol.Coerce(int),
    }
)

SEARCH_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_QUERY): cv.string,
    }
)


def _get_loaded_entry(hass: HomeAssistant) -> StockroomConfigEntry:
    """Return the first loaded Stockroom config entry."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="integration_not_loaded"
    )


def _resolve_device(hass: HomeAssistant, entity_id: str) -> dr.DeviceEntry:
    """Resolve an entity id to its Home Assistant device."""
    entity_registry = er.async_get(hass)
    entity_entry = entity_registry.async_get(entity_id)
    if entity_entry is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entity_not_found",
            translation_placeholders={"entity_id": entity_id},
        )
    if entity_entry.device_id is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entity_has_no_device",
            translation_placeholders={"entity_id": entity_id},
        )
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(entity_entry.device_id)
    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entity_has_no_device",
            translation_placeholders={"entity_id": entity_id},
        )
    return device


def _device_friendly_name(device: dr.DeviceEntry, fallback: str) -> str:
    """Return a human-friendly name for a device."""
    return device.name_by_user or device.name or fallback


def _map_api_error(err: Exception) -> HomeAssistantError:
    """Map an API exception to a user-facing Home Assistant error."""
    if isinstance(err, StockroomPermissionError):
        return ServiceValidationError(
            translation_domain=DOMAIN, translation_key="token_read_only"
        )
    if isinstance(err, StockroomValidationError):
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="validation_failed",
            translation_placeholders={"message": str(err)},
        )
    if isinstance(err, StockroomAuthenticationError):
        return ServiceValidationError(
            translation_domain=DOMAIN, translation_key="invalid_token"
        )
    if isinstance(err, StockroomConnectionError):
        return HomeAssistantError("Unable to reach the Stockroom server.")
    return HomeAssistantError(f"Stockroom request failed: {err}")


def async_setup_services(hass: HomeAssistant) -> None:
    """Register Stockroom service actions (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_LINK_ITEM):
        return

    async def _handle_link_item(call: ServiceCall) -> None:
        entry = _get_loaded_entry(hass)
        entity_id = call.data[ATTR_ENTITY_ID]
        item_id = call.data[ATTR_ITEM_ID]
        device = _resolve_device(hass, entity_id)
        friendly_name = call.data.get(ATTR_FRIENDLY_NAME) or _device_friendly_name(
            device, entity_id
        )
        try:
            new_options = await apply_link(
                hass,
                entry,
                entry.runtime_data.api,
                ha_entity_id=entity_id,
                ha_device_id=device.id,
                item_id=item_id,
                friendly_name=friendly_name,
            )
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="link_conflict",
                translation_placeholders={"detail": str(err)},
            ) from err
        except StockroomApiError as err:
            raise _map_api_error(err) from err
        hass.config_entries.async_update_entry(entry, options=new_options)

    async def _handle_unlink_item(call: ServiceCall) -> None:
        entry = _get_loaded_entry(hass)
        device = _resolve_device(hass, call.data[ATTR_ENTITY_ID])
        try:
            new_options = await remove_link(
                hass, entry, entry.runtime_data.api, device.id
            )
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_linked",
                translation_placeholders={"detail": str(err)},
            ) from err
        except StockroomApiError as err:
            raise _map_api_error(err) from err
        hass.config_entries.async_update_entry(entry, options=new_options)

    async def _handle_create_and_link_item(call: ServiceCall) -> None:
        entry = _get_loaded_entry(hass)
        entity_id = call.data[ATTR_ENTITY_ID]
        device = _resolve_device(hass, entity_id)
        name = call.data.get(ATTR_NAME) or _device_friendly_name(device, entity_id)
        api = entry.runtime_data.api
        payload: dict[str, Any] = {"name": name, "type": call.data[ATTR_TYPE]}
        if (parent_id := call.data.get(ATTR_PARENT_ID)) is not None:
            payload["parent_id"] = parent_id
        try:
            created = await api.async_create_item(payload)
            item_id = created.get("id")
            if not isinstance(item_id, int):
                raise StockroomApiError("Created item is missing an id")
            new_options = await apply_link(
                hass,
                entry,
                api,
                ha_entity_id=entity_id,
                ha_device_id=device.id,
                item_id=item_id,
                friendly_name=name,
            )
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="link_conflict",
                translation_placeholders={"detail": str(err)},
            ) from err
        except StockroomApiError as err:
            raise _map_api_error(err) from err
        hass.config_entries.async_update_entry(entry, options=new_options)

    async def _handle_search(call: ServiceCall) -> ServiceResponse:
        entry = _get_loaded_entry(hass)
        try:
            results = await entry.runtime_data.api.async_search(call.data[ATTR_QUERY])
        except StockroomApiError as err:
            raise _map_api_error(err) from err
        return {"results": results}

    hass.services.async_register(
        DOMAIN, SERVICE_LINK_ITEM, _handle_link_item, LINK_ITEM_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_UNLINK_ITEM, _handle_unlink_item, UNLINK_ITEM_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_AND_LINK_ITEM,
        _handle_create_and_link_item,
        CREATE_AND_LINK_ITEM_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH,
        _handle_search,
        SEARCH_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


def async_unload_services(hass: HomeAssistant) -> None:
    """Unregister Stockroom service actions."""
    for service in (
        SERVICE_LINK_ITEM,
        SERVICE_UNLINK_ITEM,
        SERVICE_CREATE_AND_LINK_ITEM,
        SERVICE_SEARCH,
    ):
        hass.services.async_remove(DOMAIN, service)
