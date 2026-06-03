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
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import (
    StockroomApiClient,
    StockroomAuthenticationError,
    StockroomConnectionError,
    StockroomPermissionError,
    normalize_stockroom_host,
)
from .const import (
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
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
    """Handle Stockroom options (poll interval)."""

    async def async_step_init(
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
        return self.async_show_form(step_id="init", data_schema=schema)


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate invalid authentication."""
