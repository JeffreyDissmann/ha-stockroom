"""Tests for the Stockroom config and options flow."""

from __future__ import annotations

import aiohttp
from aioresponses import aioresponses
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stockroom.const import (
    CONF_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)

from .const import MOCK_CONFIG, MOCK_HOST, URL_USER, USER_PAYLOAD


async def test_user_flow_success(hass: HomeAssistant) -> None:
    """A valid host/token creates a config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with aioresponses() as mocked:
        mocked.get(URL_USER, payload=USER_PAYLOAD)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], MOCK_CONFIG
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Stockroom"
    assert result["data"][CONF_HOST] == MOCK_HOST
    assert result["data"][CONF_TOKEN] == MOCK_CONFIG[CONF_TOKEN]


async def test_user_flow_invalid_auth(hass: HomeAssistant) -> None:
    """A 401 surfaces an invalid_auth error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with aioresponses() as mocked:
        mocked.get(URL_USER, status=401, payload={"message": "Unauthenticated."})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], MOCK_CONFIG
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    """A transport error surfaces a cannot_connect error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with aioresponses() as mocked:
        mocked.get(URL_USER, exception=aiohttp.ClientConnectionError("boom"))
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], MOCK_CONFIG
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_duplicate_aborts(hass: HomeAssistant) -> None:
    """A second entry for the same host aborts."""
    existing = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, unique_id=MOCK_HOST)
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with aioresponses() as mocked:
        mocked.get(URL_USER, payload=USER_PAYLOAD)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], MOCK_CONFIG
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_flow_updates_token(hass: HomeAssistant) -> None:
    """The reauth flow validates and stores a new token."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, unique_id=MOCK_HOST)
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with aioresponses() as mocked:
        mocked.get(URL_USER, payload=USER_PAYLOAD)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TOKEN: "new-token"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == "new-token"


async def test_options_flow_sets_scan_interval(hass: HomeAssistant) -> None:
    """The options flow stores the chosen poll interval."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, unique_id=MOCK_HOST)
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL_MINUTES: 15}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SCAN_INTERVAL_MINUTES] == 15
