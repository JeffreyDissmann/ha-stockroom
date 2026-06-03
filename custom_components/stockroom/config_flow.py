"""Config flow for the Stockroom integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    selector,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import (
    StockroomApiClient,
    StockroomApiError,
    StockroomAuthenticationError,
    StockroomConnectionError,
    StockroomPermissionError,
    normalize_stockroom_host,
)
from .const import (
    ATTR_ITEM_ID,
    ATTR_NAME,
    ATTR_PARENT_ID,
    ATTR_TYPE,
    CONF_AREA,
    CONF_DEVICE_ID,
    CONF_DEVICE_IDS,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_ITEM_TYPE,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    ITEM_TYPES,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
)
from .linking import apply_link, get_link_maps, remove_link

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_TOKEN): str,
        vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the host/token by calling ``GET /user``."""
    api = StockroomApiClient(
        data[CONF_HOST], data[CONF_TOKEN], async_get_clientsession(hass)
    )
    try:
        user = await api.async_get_user()
    except (StockroomAuthenticationError, StockroomPermissionError) as err:
        raise InvalidAuth(str(err)) from err
    except StockroomConnectionError as err:
        raise CannotConnect from err

    return {
        "title": data.get(CONF_NAME) or DEFAULT_NAME,
        "user_name": user.name,
        "unique_id": normalize_stockroom_host(data[CONF_HOST]),
    }


class StockroomConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Stockroom."""

    VERSION = 1
    _auth_error_detail: str = "No authentication attempt yet."

    @staticmethod
    def async_get_options_flow(config_entry: Any) -> StockroomOptionsFlow:
        """Return the options flow handler."""
        return StockroomOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}
        suggested_values: dict[str, Any] = {}
        connected_as: str | None = None
        if user_input is not None:
            user_input[CONF_HOST] = normalize_stockroom_host(user_input[CONF_HOST])
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth as err:
                errors["base"] = "invalid_auth"
                self._auth_error_detail = str(err)
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["unique_id"])
                self._abort_if_unique_id_configured()
                connected_as = info["user_name"]
                return self.async_create_entry(
                    title=info["title"],
                    data=user_input,
                    description_placeholders={"user_name": connected_as},
                )
            suggested_values = {
                CONF_HOST: user_input.get(CONF_HOST, ""),
                CONF_NAME: user_input.get(CONF_NAME, DEFAULT_NAME),
            }

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, suggested_values
            ),
            errors=errors,
            description_placeholders={"auth_error_detail": self._auth_error_detail},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when the token becomes invalid."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm re-authentication with a new token."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            candidate = {**reauth_entry.data, CONF_TOKEN: user_input[CONF_TOKEN]}
            try:
                await validate_input(self.hass, candidate)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth as err:
                errors["base"] = "invalid_auth"
                self._auth_error_detail = str(err)
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(reauth_entry, data=candidate)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            errors=errors,
            description_placeholders={
                "host": reauth_entry.data[CONF_HOST],
                "auth_error_detail": self._auth_error_detail,
            },
        )


class StockroomOptionsFlow(OptionsFlow):
    """Options flow: device<->item linking wizard plus poll interval."""

    def __init__(self) -> None:
        """Initialize the wizard's transient state."""
        self._selected_device_id: str | None = None
        self._selected_area_id: str | None = None
        self._bulk_device_ids: list[str] = []
        self._pending_options: dict[str, Any] | None = None

    @property
    def _api(self) -> StockroomApiClient:
        """Return the API client from the loaded coordinator."""
        return self.config_entry.runtime_data.api

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the linking wizard menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "link_device",
                "create_and_link",
                "unlink_device",
                "bulk_create_from_area",
                "settings",
            ],
        )

    # -- Settings (poll interval) ----------------------------------------

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the poll interval option."""
        if user_input is not None:
            return self.async_create_entry(
                data={**self.config_entry.options, **user_input}
            )

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL_MINUTES, default=current
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL_MINUTES,
                        max=MAX_SCAN_INTERVAL_MINUTES,
                        step=1,
                        unit_of_measurement="minutes",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                )
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    # -- Link an existing item -------------------------------------------

    async def async_step_link_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select an unlinked Home Assistant device to link."""
        errors: dict[str, str] = {}
        if user_input is not None:
            error = self._validate_linkable_device(user_input[CONF_DEVICE_ID])
            if error:
                errors["base"] = error
            else:
                self._selected_device_id = user_input[CONF_DEVICE_ID]
                return await self.async_step_link_select_item()

        return self.async_show_form(
            step_id="link_device",
            data_schema=vol.Schema(
                {vol.Required(CONF_DEVICE_ID): selector.DeviceSelector()}
            ),
            errors=errors,
        )

    async def async_step_link_select_item(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select an existing Stockroom item to link the device to."""
        device_id = self._selected_device_id
        if device_id is None:
            return self.async_abort(reason="missing_device")

        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._async_try_link(device_id, int(user_input[ATTR_ITEM_ID]))
            if error is None:
                return self.async_create_entry(
                    title="",
                    data=self._pending_options or dict(self.config_entry.options),
                )
            errors["base"] = error

        try:
            options = await self._async_item_options(has_ha_link=0)
        except StockroomApiError:
            return self.async_abort(reason="cannot_connect")
        if not options:
            return self.async_abort(reason="no_unlinked_items")

        return self.async_show_form(
            step_id="link_select_item",
            data_schema=vol.Schema(
                {
                    vol.Required(ATTR_ITEM_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={"device_name": self._device_name(device_id)},
        )

    # -- Create and link a new item --------------------------------------

    async def async_step_create_and_link(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select an unlinked device to create a new Stockroom item for."""
        errors: dict[str, str] = {}
        if user_input is not None:
            error = self._validate_linkable_device(user_input[CONF_DEVICE_ID])
            if error:
                errors["base"] = error
            else:
                self._selected_device_id = user_input[CONF_DEVICE_ID]
                return await self.async_step_create_item_details()

        return self.async_show_form(
            step_id="create_and_link",
            data_schema=vol.Schema(
                {vol.Required(CONF_DEVICE_ID): selector.DeviceSelector()}
            ),
            errors=errors,
        )

    async def async_step_create_item_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter details for the new Stockroom item, then create and link it."""
        device_id = self._selected_device_id
        if device_id is None:
            return self.async_abort(reason="missing_device")

        errors: dict[str, str] = {}
        if user_input is not None:
            payload: dict[str, Any] = {
                "name": user_input[ATTR_NAME],
                "type": user_input[ATTR_TYPE],
            }
            if user_input.get(ATTR_PARENT_ID):
                payload["parent_id"] = int(user_input[ATTR_PARENT_ID])
            error = await self._async_create_and_link(
                device_id, payload, user_input[ATTR_NAME]
            )
            if error is None:
                return self.async_create_entry(
                    title="",
                    data=self._pending_options or dict(self.config_entry.options),
                )
            errors["base"] = error

        try:
            parent_options = await self._async_item_options(types=("room", "container"))
        except StockroomApiError:
            parent_options = []

        schema_dict: dict[Any, Any] = {
            vol.Required(ATTR_NAME, default=self._device_name(device_id)): str,
            vol.Required(ATTR_TYPE, default=DEFAULT_ITEM_TYPE): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=item_type, label=item_type)
                        for item_type in ITEM_TYPES
                    ],
                    translation_key="item_type",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }
        if parent_options:
            schema_dict[vol.Optional(ATTR_PARENT_ID)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=parent_options, mode=selector.SelectSelectorMode.DROPDOWN
                )
            )

        return self.async_show_form(
            step_id="create_item_details",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
            description_placeholders={"device_name": self._device_name(device_id)},
        )

    # -- Unlink ----------------------------------------------------------

    async def async_step_unlink_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a linked device to unlink."""
        ha_device_to_item, _ = get_link_maps(self.config_entry)
        if not ha_device_to_item:
            return self.async_abort(reason="no_links")

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                new_options = await remove_link(
                    self.hass, self.config_entry, self._api, user_input[CONF_DEVICE_ID]
                )
            except ValueError:
                errors["base"] = "not_linked"
            except StockroomPermissionError:
                errors["base"] = "token_read_only"
            except StockroomApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="", data=new_options)

        options = sorted(
            (
                selector.SelectOptionDict(
                    value=device_id,
                    label=f"{self._device_name(device_id)} (item #{item_id})",
                )
                for device_id, item_id in ha_device_to_item.items()
            ),
            key=lambda option: option["label"].lower(),
        )
        return self.async_show_form(
            step_id="unlink_device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options, mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
            errors=errors,
        )

    # -- Bulk create from an area ----------------------------------------

    async def async_step_bulk_create_from_area(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a Home Assistant area for bulk item creation."""
        if user_input is not None:
            self._selected_area_id = user_input[CONF_AREA]
            return await self.async_step_bulk_select_devices()

        return self.async_show_form(
            step_id="bulk_create_from_area",
            data_schema=vol.Schema({vol.Required(CONF_AREA): selector.AreaSelector()}),
        )

    async def async_step_bulk_select_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the unlinked devices in the area to create items for."""
        area_id = self._selected_area_id
        if area_id is None:
            return self.async_abort(reason="missing_area")

        candidates = self._unlinked_devices_in_area(area_id)
        if not candidates:
            return self.async_abort(reason="no_unlinked_ha_devices")

        errors: dict[str, str] = {}
        if user_input is not None:
            selected = user_input.get(CONF_DEVICE_IDS) or []
            if not selected:
                errors["base"] = "no_devices_selected"
            else:
                self._bulk_device_ids = selected
                return await self.async_step_bulk_create_details()

        options = sorted(
            (
                selector.SelectOptionDict(
                    value=device_id, label=self._device_name(device_id)
                )
                for device_id in candidates
            ),
            key=lambda option: option["label"].lower(),
        )
        area = ar.async_get(self.hass).async_get_area(area_id)
        return self.async_show_form(
            step_id="bulk_select_devices",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_IDS, default=[]): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={"area_name": area.name if area else area_id},
        )

    async def async_step_bulk_create_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create and link an item for each selected device."""
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._async_bulk_create(
                self._bulk_device_ids, user_input[ATTR_TYPE]
            )
            if error is None:
                return self.async_create_entry(
                    title="",
                    data=self._pending_options or dict(self.config_entry.options),
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="bulk_create_details",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        ATTR_TYPE, default=DEFAULT_ITEM_TYPE
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=t, label=t)
                                for t in ITEM_TYPES
                            ],
                            translation_key="item_type",
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={"count": str(len(self._bulk_device_ids))},
        )

    # -- Shared helpers --------------------------------------------------

    def _validate_linkable_device(self, device_id: str) -> str | None:
        """Return an error key if the device cannot be linked, else None."""
        ha_device_to_item, _ = get_link_maps(self.config_entry)
        if device_id in ha_device_to_item:
            return "device_already_linked"
        if _primary_entity_id(self.hass, device_id) is None:
            return "device_has_no_entity"
        return None

    async def _async_try_link(self, device_id: str, item_id: int) -> str | None:
        """Link a device to an item, returning an error key on failure."""
        entity_id = _primary_entity_id(self.hass, device_id)
        if entity_id is None:
            return "device_has_no_entity"
        try:
            self._pending_options = await apply_link(
                self.hass,
                self.config_entry,
                self._api,
                ha_entity_id=entity_id,
                ha_device_id=device_id,
                item_id=item_id,
                friendly_name=self._device_name(device_id),
            )
        except ValueError:
            return "link_conflict"
        except StockroomPermissionError:
            return "token_read_only"
        except StockroomApiError:
            return "cannot_connect"
        return None

    async def _async_create_and_link(
        self, device_id: str, payload: dict[str, Any], name: str
    ) -> str | None:
        """Create a Stockroom item and link it, returning an error key on failure."""
        entity_id = _primary_entity_id(self.hass, device_id)
        if entity_id is None:
            return "device_has_no_entity"
        try:
            created = await self._api.async_create_item(payload)
            item_id = created.get("id")
            if not isinstance(item_id, int):
                return "create_failed"
            self._pending_options = await apply_link(
                self.hass,
                self.config_entry,
                self._api,
                ha_entity_id=entity_id,
                ha_device_id=device_id,
                item_id=item_id,
                friendly_name=name,
            )
        except ValueError:
            return "link_conflict"
        except StockroomPermissionError:
            return "token_read_only"
        except StockroomApiError:
            return "create_failed"
        return None

    async def _async_bulk_create(
        self, device_ids: list[str], item_type: str
    ) -> str | None:
        """Create and link an item for each device, threading options through."""
        new_options: dict[str, Any] | None = None
        try:
            for device_id in device_ids:
                ha_map, _ = get_link_maps(self.config_entry, new_options)
                if device_id in ha_map:
                    continue
                entity_id = _primary_entity_id(self.hass, device_id)
                if entity_id is None:
                    continue
                created = await self._api.async_create_item(
                    {"name": self._device_name(device_id), "type": item_type}
                )
                item_id = created.get("id")
                if not isinstance(item_id, int):
                    continue
                new_options = await apply_link(
                    self.hass,
                    self.config_entry,
                    self._api,
                    ha_entity_id=entity_id,
                    ha_device_id=device_id,
                    item_id=item_id,
                    friendly_name=self._device_name(device_id),
                    options=new_options,
                )
        except StockroomPermissionError:
            return "token_read_only"
        except StockroomApiError:
            return "create_failed"
        self._pending_options = new_options
        return None

    async def _async_item_options(
        self, *, has_ha_link: int | None = None, types: tuple[str, ...] = ()
    ) -> list[selector.SelectOptionDict]:
        """Fetch items and build dropdown options labelled by name and location."""
        items: list[dict[str, Any]] = []
        if types:
            for item_type in types:
                items.extend(await self._api.async_get_all_items(type=item_type))
        else:
            items.extend(await self._api.async_get_all_items(has_ha_link=has_ha_link))

        options: list[selector.SelectOptionDict] = []
        for item in items:
            item_id = item.get("id")
            if not isinstance(item_id, int):
                continue
            name = item.get("name") or f"Item {item_id}"
            path = item.get("location_path") or ""
            label = f"{name} — {path}" if path else str(name)
            options.append(selector.SelectOptionDict(value=str(item_id), label=label))
        options.sort(key=lambda option: option["label"].lower())
        return options

    def _unlinked_devices_in_area(self, area_id: str) -> list[str]:
        """Return ids of unlinked, entity-bearing devices in an area."""
        ha_device_to_item, _ = get_link_maps(self.config_entry)
        device_registry = dr.async_get(self.hass)
        return [
            device.id
            for device in dr.async_entries_for_area(device_registry, area_id)
            if device.id not in ha_device_to_item
            and _primary_entity_id(self.hass, device.id) is not None
        ]

    def _device_name(self, device_id: str) -> str:
        """Return a human-friendly name for a device."""
        device = dr.async_get(self.hass).async_get(device_id)
        if device is None:
            return device_id
        return device.name_by_user or device.name or device_id


def _primary_entity_id(hass: HomeAssistant, device_id: str) -> str | None:
    """Return a representative entity id for a device, preferring a primary one."""
    registry = er.async_get(hass)
    entries = er.async_entries_for_device(
        registry, device_id, include_disabled_entities=True
    )
    if not entries:
        return None
    for entry in entries:
        if not entry.disabled and entry.entity_category is None:
            return entry.entity_id
    return entries[0].entity_id


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate invalid authentication."""
