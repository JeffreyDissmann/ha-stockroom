"""Config flow for the Stockroom integration."""

from __future__ import annotations

from collections import Counter
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
    instance_id,
    selector,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import (
    StockroomApiClient,
    StockroomApiError,
    StockroomAuthenticationError,
    StockroomConnectionError,
    StockroomNotFoundError,
    StockroomPermissionError,
    normalize_stockroom_host,
)
from .battery import battery_notes_type_for_device
from .const import (
    ATTR_BATTERY_TYPE,
    ATTR_DESCRIPTION,
    ATTR_ITEM_ID,
    ATTR_MANUFACTURER,
    ATTR_MODEL_NUMBER,
    ATTR_NAME,
    ATTR_PARENT_ID,
    ATTR_QUERY,
    ATTR_SERIAL_NUMBER,
    ATTR_TYPE,
    CONF_AREA,
    CONF_DEVICE_ID,
    CONF_DEVICE_IDS,
    CONF_REPLACE,
    CONF_ROOM_ID,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_ITEM_TYPE,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    ITEM_TYPES,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
)
from .linking import (
    apply_link,
    build_updated_area_options,
    build_updated_options,
    device_friendly_name,
    get_area_room_map,
    get_link_maps,
    primary_entity_id,
    remove_link,
    resolve_device_id,
)

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
    except StockroomApiError as err:
        # e.g. a 5xx, an unexpected status, or a non-JSON body.
        raise UnexpectedResponse(str(err)) from err

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
            except UnexpectedResponse as err:
                _LOGGER.error("Stockroom returned an unexpected response: %s", err)
                errors["base"] = "api_error"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["unique_id"])
                # The entry carries an update listener that owns reloading, so the
                # flow must not reload as well (a hard error from HA 2026.12).
                self._abort_if_unique_id_configured(reload_on_update=False)
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
            except UnexpectedResponse as err:
                _LOGGER.error("Stockroom returned an unexpected response: %s", err)
                errors["base"] = "api_error"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Not async_update_reload_and_abort: the entry's update listener
                # already reloads on a data change, and combining the two is a
                # hard error from HA 2026.12.
                return self.async_update_and_abort(reauth_entry, data=candidate)

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
        self._search_results: list[selector.SelectOptionDict] = []
        self._browse_room_id: int | None = None
        self._pending_item_id: int | None = None
        self._pending_conflict_name: str | None = None
        self._repair_refresh: list[tuple[str, int]] = []
        self._repair_delete: list[tuple[int, str]] = []
        self._repair_adopt: dict[str, dict[str, Any]] = {}
        self._repair_drop: list[str] = []

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
                "link_area",
                "repair",
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
                data={
                    **self.config_entry.options,
                    CONF_SCAN_INTERVAL_MINUTES: int(
                        user_input[CONF_SCAN_INTERVAL_MINUTES]
                    ),
                }
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
                return await self.async_step_link_method()

        return self.async_show_form(
            step_id="link_device",
            data_schema=vol.Schema(
                {vol.Required(CONF_DEVICE_ID): selector.DeviceSelector()}
            ),
            errors=errors,
        )

    async def async_step_link_method(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose how to find the Stockroom item to link."""
        return self.async_show_menu(
            step_id="link_method",
            menu_options=["link_search", "link_browse", "link_enter_id"],
            description_placeholders={
                "device_name": self._device_name(self._selected_device_id)
            },
        )

    async def async_step_link_search(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Search Stockroom by name and show the top matches."""
        errors: dict[str, str] = {}
        if user_input is not None:
            query = (user_input.get(ATTR_QUERY) or "").strip()
            if not query:
                errors["base"] = "no_results"
            else:
                try:
                    self._search_results = await self._async_search_options(query)
                except StockroomApiError:
                    errors["base"] = "cannot_connect"
                else:
                    if self._search_results:
                        return await self.async_step_link_search_results()
                    errors["base"] = "no_results"

        return self.async_show_form(
            step_id="link_search",
            data_schema=vol.Schema({vol.Required(ATTR_QUERY): selector.TextSelector()}),
            errors=errors,
            description_placeholders={
                "device_name": self._device_name(self._selected_device_id)
            },
        )

    async def async_step_link_search_results(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick an item from the search results."""
        if user_input is not None:
            return await self._async_select_item(int(user_input[ATTR_ITEM_ID]))

        return self.async_show_form(
            step_id="link_search_results",
            data_schema=vol.Schema(
                {
                    vol.Required(ATTR_ITEM_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=self._search_results,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            description_placeholders={
                "device_name": self._device_name(self._selected_device_id)
            },
        )

    async def async_step_link_browse(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a room to browse for an unlinked item."""
        if user_input is not None:
            self._browse_room_id = int(user_input[CONF_ROOM_ID])
            return await self.async_step_link_browse_items()

        try:
            rooms = await self._api.async_get_rooms()
        except StockroomApiError:
            return self.async_abort(reason="cannot_connect")
        options = _build_unique_options(
            [
                (room["id"], _node_label(room, path_key="location_path"))
                for room in rooms
                if isinstance(room.get("id"), int)
            ]
        )
        if not options:
            return self.async_abort(reason="no_rooms")

        return self.async_show_form(
            step_id="link_browse",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ROOM_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options, mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
        )

    async def async_step_link_browse_items(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick an unlinked item within the chosen room's subtree."""
        if user_input is not None:
            return await self._async_select_item(int(user_input[ATTR_ITEM_ID]))

        try:
            items = await self._api.async_get_all_items(
                room=self._browse_room_id, has_ha_link=0
            )
        except StockroomApiError:
            return self.async_abort(reason="cannot_connect")
        options = self._result_options(items, path_key="location_path")
        if not options:
            return self.async_abort(reason="no_unlinked_items")

        return self.async_show_form(
            step_id="link_browse_items",
            data_schema=vol.Schema(
                {
                    vol.Required(ATTR_ITEM_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options, mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
        )

    async def async_step_link_enter_id(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter a Stockroom item ID directly."""
        if user_input is not None:
            return await self._async_select_item(int(user_input[ATTR_ITEM_ID]))

        return self.async_show_form(
            step_id="link_enter_id",
            data_schema=vol.Schema(
                {
                    vol.Required(ATTR_ITEM_ID): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, step=1, mode=selector.NumberSelectorMode.BOX
                        )
                    )
                }
            ),
            description_placeholders={
                "device_name": self._device_name(self._selected_device_id)
            },
        )

    async def async_step_link_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm replacing a link owned by another Home Assistant instance."""
        if user_input is not None:
            if user_input.get(CONF_REPLACE):
                return await self._async_do_link_and_finish(self._pending_item_id)
            return self.async_abort(reason="link_not_replaced")

        return self.async_show_form(
            step_id="link_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_REPLACE, default=False): selector.BooleanSelector()}
            ),
            description_placeholders={
                "device_name": self._device_name(self._selected_device_id),
                "item_id": str(self._pending_item_id),
                "friendly_name": self._pending_conflict_name or "another instance",
            },
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
            for field in (
                ATTR_MANUFACTURER,
                ATTR_MODEL_NUMBER,
                ATTR_SERIAL_NUMBER,
                ATTR_DESCRIPTION,
            ):
                value = (user_input.get(field) or "").strip()
                if value:
                    payload[field] = value
            if user_input.get(ATTR_PARENT_ID):
                payload["parent_id"] = int(user_input[ATTR_PARENT_ID])
            battery_type = (user_input.get(ATTR_BATTERY_TYPE) or "").strip()
            error = await self._async_create_and_link(
                device_id,
                payload,
                user_input[ATTR_NAME],
                battery_type=battery_type or None,
            )
            if error is None:
                return self.async_create_entry(
                    title="",
                    data=self._pending_options or dict(self.config_entry.options),
                )
            errors["base"] = error

        try:
            parent_options = await self._async_parent_options()
        except StockroomApiError:
            parent_options = []

        details = self._device_details(device_id)
        battery_type_default = (
            battery_notes_type_for_device(
                self.hass, device_id, self.config_entry.entry_id
            )
            or ""
        )
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
            vol.Optional(
                ATTR_MANUFACTURER, default=details.get(ATTR_MANUFACTURER, "")
            ): str,
            vol.Optional(
                ATTR_MODEL_NUMBER, default=details.get(ATTR_MODEL_NUMBER, "")
            ): str,
            vol.Optional(
                ATTR_SERIAL_NUMBER, default=details.get(ATTR_SERIAL_NUMBER, "")
            ): str,
            vol.Optional(ATTR_BATTERY_TYPE, default=battery_type_default): str,
            vol.Optional(ATTR_DESCRIPTION, default=""): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
        }
        if parent_options:
            mapped_room = self._mapped_room_for_device(device_id)
            valid_values = {option["value"] for option in parent_options}
            if mapped_room is not None and str(mapped_room) in valid_values:
                parent_marker: Any = vol.Optional(
                    ATTR_PARENT_ID, default=str(mapped_room)
                )
            else:
                parent_marker = vol.Optional(ATTR_PARENT_ID)
            schema_dict[parent_marker] = selector.SelectSelector(
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
        if user_input is not None:
            error = await self._async_bulk_create(
                self._bulk_device_ids, user_input[ATTR_TYPE]
            )
            if error is None:
                return self.async_create_entry(
                    title="",
                    data=self._pending_options or dict(self.config_entry.options),
                )
            # Persist any items created before the failure so they aren't
            # orphaned (or re-created on retry), then report the error.
            if self._pending_options is not None:
                self.hass.config_entries.async_update_entry(
                    self.config_entry, options=self._pending_options
                )
            return self.async_abort(reason=error)

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
            description_placeholders={"count": str(len(self._bulk_device_ids))},
        )

    # -- Link an area to a room ------------------------------------------

    async def async_step_link_area(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Map a Home Assistant area to a Stockroom room (for auto-placement)."""
        if user_input is not None:
            area_room = get_area_room_map(self.config_entry)
            room_raw = user_input.get(CONF_ROOM_ID) or ""
            if room_raw:
                area_room[user_input[CONF_AREA]] = int(room_raw)
            else:
                area_room.pop(user_input[CONF_AREA], None)
            return self.async_create_entry(
                title="",
                data=build_updated_area_options(self.config_entry, area_room),
            )

        try:
            rooms = await self._api.async_get_rooms()
        except StockroomApiError:
            return self.async_abort(reason="cannot_connect")
        room_options = _build_unique_options(
            [
                (room["id"], _node_label(room, path_key="location_path"))
                for room in rooms
                if isinstance(room.get("id"), int)
            ]
        )
        if not room_options:
            return self.async_abort(reason="no_rooms")
        # A blank choice clears any existing mapping for the chosen area.
        options = [
            selector.SelectOptionDict(value="", label="—"),
            *room_options,
        ]

        return self.async_show_form(
            step_id="link_area",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_AREA): selector.AreaSelector(),
                    vol.Required(CONF_ROOM_ID, default=""): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options, mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    ),
                }
            ),
        )

    # -- Repair links ----------------------------------------------------

    async def async_step_repair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconcile Stockroom links with Home Assistant, then apply."""
        if user_input is not None:
            return await self._async_apply_repair(user_input.get("adopt") or [])

        try:
            await self._async_scan_repair()
        except StockroomApiError:
            return self.async_abort(reason="cannot_connect")

        if not (
            self._repair_refresh
            or self._repair_delete
            or self._repair_adopt
            or self._repair_drop
        ):
            # Links are already in sync, but still honour the explicit click by
            # re-pushing battery level/type for the linked items.
            await self._async_resync_batteries()
            return self.async_abort(reason="nothing_to_repair")

        schema_dict: dict[Any, Any] = {}
        if self._repair_adopt:
            options = sorted(
                (
                    selector.SelectOptionDict(value=device_id, label=info["label"])
                    for device_id, info in self._repair_adopt.items()
                ),
                key=lambda option: option["label"].lower(),
            )
            schema_dict[vol.Optional("adopt", default=list(self._repair_adopt))] = (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                )
            )

        return self.async_show_form(
            step_id="repair",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "refresh_count": str(len(self._repair_refresh)),
                "remove_count": str(len(self._repair_delete) + len(self._repair_drop)),
                "adopt_count": str(len(self._repair_adopt)),
            },
        )

    async def _async_scan_repair(self) -> None:
        """Fetch all Stockroom links and classify them into a repair plan."""
        self._repair_refresh = []
        self._repair_delete = []
        self._repair_adopt = {}
        self._repair_drop = []

        links = await self._api.async_get_ha_links()
        server: dict[str, dict[str, Any]] = {}
        for element in links:
            link = element.get("home_assistant_link")
            if not isinstance(link, dict):
                continue
            device_id = link.get("ha_device_id")
            item_id = element.get("id")
            if not isinstance(device_id, str) or not device_id:
                continue  # only device-based links are managed here
            if not isinstance(item_id, int):
                continue
            server[device_id] = {
                "item_id": item_id,
                "friendly_name": link.get("friendly_name"),
                "name": element.get("name"),
                "location_path": element.get("location_path"),
            }

        local_device_to_item, _ = get_link_maps(self.config_entry)

        # The server may still hold device ids from before HA 2026.8 split devices
        # per config entry; compare and repair on the live ids.
        resolved_server: dict[str, dict[str, Any]] = {}
        for device_id, info in server.items():
            resolved_id = resolve_device_id(
                self.hass, device_id, self.config_entry.entry_id
            )
            if resolved_id is None:
                self._repair_delete.append((info["item_id"], device_id))
                continue
            resolved_server[resolved_id] = info

        for device_id, info in resolved_server.items():
            item_id = info["item_id"]
            if device_id in local_device_to_item:
                self._repair_refresh.append((device_id, item_id))
            else:
                label = info["friendly_name"] or info["name"] or device_id
                if info["location_path"]:
                    label = f"{label} — {info['location_path']}"
                self._repair_adopt[device_id] = {"item_id": item_id, "label": label}

        # Local links the server doesn't know about (device renamed away / link lost).
        for device_id, item_id in local_device_to_item.items():
            if device_id in resolved_server:
                continue
            if (
                resolve_device_id(self.hass, device_id, self.config_entry.entry_id)
                is None
            ):
                self._repair_drop.append(device_id)
            else:
                self._repair_refresh.append((device_id, item_id))

    async def _async_apply_repair(self, selected_adopt: list[str]) -> ConfigFlowResult:
        """Apply the repair plan: refresh, adopt selected, delete stale."""
        new_options: dict[str, Any] = dict(self.config_entry.options)
        to_apply = list(self._repair_refresh)
        to_apply.extend(
            (device_id, self._repair_adopt[device_id]["item_id"])
            for device_id in selected_adopt
            if device_id in self._repair_adopt
        )

        try:
            for device_id, item_id in to_apply:
                entity_id = primary_entity_id(
                    self.hass, device_id, self.config_entry.entry_id
                )
                if entity_id is None:
                    continue  # cannot form a valid link without an entity
                try:
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
                except ValueError:
                    continue  # 1:1 conflict — skip this one

            for item_id, device_id in self._repair_delete:
                await self._api.async_delete_item_ha_link(item_id)
                new_options = self._drop_device(new_options, device_id, item_id)
        except StockroomPermissionError:
            return self.async_abort(reason="token_read_only")
        except StockroomApiError:
            return self.async_abort(reason="cannot_connect")

        for device_id in self._repair_drop:
            new_options = self._drop_device(new_options, device_id)

        # Re-push battery level/type for the linked items as part of the repair.
        await self._async_resync_batteries()

        return self.async_create_entry(title="", data=new_options)

    async def _async_resync_batteries(self) -> None:
        """Force the running battery sync to re-push level/type for linked items."""
        battery_sync = getattr(self.config_entry.runtime_data, "battery_sync", None)
        if battery_sync is not None:
            await battery_sync.async_resync_now()

    def _drop_device(
        self, options: dict[str, Any], device_id: str, item_id: int | None = None
    ) -> dict[str, Any]:
        """Remove a device (and its item) from the link maps in ``options``."""
        device_to_item, item_to_device = get_link_maps(self.config_entry, options)
        mapped = device_to_item.pop(device_id, None)
        target = item_id if item_id is not None else mapped
        # Only drop the reverse entry if it still points back at this device, so
        # a concurrently re-adopted item isn't clobbered.
        if target is not None and item_to_device.get(target) == device_id:
            item_to_device.pop(target, None)
        return build_updated_options(
            self.config_entry, device_to_item, item_to_device, options
        )

    # -- Shared helpers --------------------------------------------------

    def _validate_linkable_device(self, device_id: str) -> str | None:
        """Return an error key if the device cannot be linked, else None."""
        ha_device_to_item, _ = get_link_maps(self.config_entry)
        if device_id in ha_device_to_item:
            return "device_already_linked"
        if primary_entity_id(self.hass, device_id, self.config_entry.entry_id) is None:
            return "device_has_no_entity"
        return None

    async def _async_try_link(self, device_id: str, item_id: int) -> str | None:
        """Link a device to an item, returning an error key on failure."""
        entity_id = primary_entity_id(self.hass, device_id, self.config_entry.entry_id)
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
        self,
        device_id: str,
        payload: dict[str, Any],
        name: str,
        battery_type: str | None = None,
    ) -> str | None:
        """Create a Stockroom item and link it, returning an error key on failure."""
        entity_id = primary_entity_id(self.hass, device_id, self.config_entry.entry_id)
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

        # Battery type is a separate PATCH (not a create field) and best-effort:
        # the item is already created/linked, and the battery sync would set it
        # from Battery Notes anyway, so a failure here must not fail the flow.
        if battery_type is not None:
            try:
                await self._api.async_set_battery_type(item_id, battery_type)
            except StockroomApiError as err:
                _LOGGER.warning(
                    "Created Stockroom item %s but could not set its battery type: %s",
                    item_id,
                    err,
                )
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
                entity_id = primary_entity_id(
                    self.hass, device_id, self.config_entry.entry_id
                )
                if entity_id is None:
                    continue
                payload: dict[str, Any] = {
                    "name": self._device_name(device_id),
                    "type": item_type,
                    **self._device_details(device_id),
                }
                if (room := self._mapped_room_for_device(device_id)) is not None:
                    payload["parent_id"] = room
                created = await self._api.async_create_item(payload)
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
                # Capture progress after each success so a mid-loop failure
                # doesn't orphan items already created and linked in Stockroom.
                self._pending_options = new_options
        except StockroomPermissionError:
            return "token_read_only"
        except StockroomApiError:
            return "create_failed"
        return None

    def _result_options(
        self,
        raw: list[dict[str, Any]],
        *,
        path_key: str,
        extra_exclude: set[int] = frozenset(),
    ) -> list[selector.SelectOptionDict]:
        """Build item options, excluding linked items, labelled unambiguously."""
        _, item_to_ha_device = get_link_maps(self.config_entry)
        excluded = set(item_to_ha_device) | set(extra_exclude)
        entries: list[tuple[int, str]] = []
        for item in raw:
            item_id = item.get("id")
            if not isinstance(item_id, int) or item_id in excluded:
                continue
            entries.append((item_id, _node_label(item, path_key=path_key)))
        return _build_unique_options(entries)

    async def _async_linked_item_ids(self) -> set[int]:
        """Return ids of items already linked to Home Assistant in Stockroom."""
        items = await self._api.async_get_all_items(has_ha_link=1)
        return {item["id"] for item in items if isinstance(item.get("id"), int)}

    async def _async_search_options(
        self, query: str
    ) -> list[selector.SelectOptionDict]:
        """Search Stockroom, excluding items already linked to Home Assistant."""
        results = await self._api.async_search(query)
        linked_ids = await self._async_linked_item_ids()
        return self._result_options(results, path_key="path", extra_exclude=linked_ids)

    async def _async_parent_options(self) -> list[selector.SelectOptionDict]:
        """Build options for rooms and containers usable as a parent."""
        items: list[dict[str, Any]] = []
        for item_type in ("room", "container"):
            items.extend(await self._api.async_get_all_items(type=item_type))
        entries: list[tuple[int, str]] = [
            (item["id"], _node_label(item, path_key="location_path"))
            for item in items
            if isinstance(item.get("id"), int)
        ]
        return _build_unique_options(entries)

    async def _async_link_status(self, item_id: int) -> tuple[str, str | None]:
        """Return ('free'|'ours'|'elsewhere', friendly_name) for an item.

        Raises a Stockroom API error if the item cannot be fetched.
        """
        item = await self._api.async_get_item(item_id)
        link = item.get("home_assistant_link")
        if not isinstance(link, dict):
            return "free", None
        our_instance = await instance_id.async_get(self.hass)
        if link.get("instance_id") == our_instance:
            return "ours", None
        name = link.get("friendly_name")
        return "elsewhere", name if isinstance(name, str) and name else None

    async def _async_select_item(self, item_id: int) -> ConfigFlowResult:
        """Run the safety check, then link or route to a replace confirmation."""
        try:
            status, friendly_name = await self._async_link_status(item_id)
        except StockroomNotFoundError:
            return self.async_abort(reason="item_not_found")
        except StockroomApiError:
            return self.async_abort(reason="cannot_connect")
        if status == "elsewhere":
            self._pending_item_id = item_id
            self._pending_conflict_name = friendly_name
            return await self.async_step_link_confirm()
        return await self._async_do_link_and_finish(item_id)

    async def _async_do_link_and_finish(self, item_id: int) -> ConfigFlowResult:
        """Create the link and finish, or abort with a clear reason."""
        error = await self._async_try_link(self._selected_device_id, item_id)
        if error == "link_conflict":
            return self.async_abort(reason="link_conflict")
        if error == "token_read_only":
            return self.async_abort(reason="token_read_only")
        if error is not None:
            return self.async_abort(reason="cannot_connect")
        return self.async_create_entry(
            title="", data=self._pending_options or dict(self.config_entry.options)
        )

    def _unlinked_devices_in_area(self, area_id: str) -> list[str]:
        """Return ids of unlinked, entity-bearing devices in an area."""
        ha_device_to_item, _ = get_link_maps(self.config_entry)
        device_registry = dr.async_get(self.hass)
        return [
            device.id
            for device in dr.async_entries_for_area(device_registry, area_id)
            if device.id not in ha_device_to_item
            and primary_entity_id(self.hass, device.id, self.config_entry.entry_id)
            is not None
        ]

    def _device_name(self, device_id: str) -> str:
        """Return a human-friendly name for a device."""
        return device_friendly_name(self.hass, device_id)

    def _device_details(self, device_id: str) -> dict[str, str]:
        """Return Stockroom item fields derived from a Home Assistant device."""
        device = dr.async_get(self.hass).async_get(device_id)
        if device is None:
            return {}
        details: dict[str, str] = {}
        if device.manufacturer:
            details[ATTR_MANUFACTURER] = device.manufacturer
        if model := (device.model or getattr(device, "model_id", None)):
            details[ATTR_MODEL_NUMBER] = model
        if serial := getattr(device, "serial_number", None):
            details[ATTR_SERIAL_NUMBER] = serial
        return details

    def _mapped_room_for_device(self, device_id: str | None) -> int | None:
        """Return the Stockroom room mapped to the device's HA area, if any."""
        if device_id is None:
            return None
        device = dr.async_get(self.hass).async_get(device_id)
        if device is None or device.area_id is None:
            return None
        return get_area_room_map(self.config_entry).get(device.area_id)


def _node_label(node: dict[str, Any], *, path_key: str) -> str:
    """Build a 'Name — location' label for a room/item.

    Stockroom's ``location_path`` is the path to the node's *location* (its
    ancestors), so it usually omits the node's own name. Show the name, with the
    location for context — unless the path already ends with the name (some
    nodes return a full self-inclusive path) or equals it.
    """
    node_id = node.get("id")
    name = str(node.get("name") or f"#{node_id}")
    path = str(node.get(path_key) or "").strip()
    if not path or path == name:
        return name
    if path.endswith(name):
        return path
    return f"{name} — {path}"


def _build_unique_options(
    entries: list[tuple[int, str]],
) -> list[selector.SelectOptionDict]:
    """Build sorted dropdown options, deduped by id and disambiguated by label.

    Stockroom can hold several rooms/items sharing a name (and location path).
    Entries are first deduplicated by id (in case a list is returned more than
    once), then any label shared by multiple ids gets an ``(#id)`` suffix so
    every option is distinguishable.
    """
    seen: set[int] = set()
    unique: list[tuple[int, str]] = []
    for item_id, label in entries:
        if item_id in seen:
            continue
        seen.add(item_id)
        unique.append((item_id, label))

    label_counts = Counter(label for _, label in unique)
    options = [
        selector.SelectOptionDict(
            value=str(item_id),
            label=f"{label} (#{item_id})" if label_counts[label] > 1 else label,
        )
        for item_id, label in unique
    ]
    options.sort(key=lambda option: option["label"].lower())
    return options


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate invalid authentication."""


class UnexpectedResponse(HomeAssistantError):
    """Stockroom answered, but with an unexpected response (e.g. a 5xx)."""
