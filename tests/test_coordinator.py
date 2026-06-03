"""Tests for Stockroom setup, the coordinator, and statistics sensors."""

from __future__ import annotations

import aiohttp
from aioresponses import aioresponses
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stockroom.const import DOMAIN

from .const import MOCK_CONFIG, MOCK_HOST, STATISTICS_PAYLOAD, URL_STATISTICS


async def _setup(hass: HomeAssistant, mocked: aioresponses) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, unique_id=MOCK_HOST)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_statistics_sensors(hass: HomeAssistant) -> None:
    """Setup loads the entry and creates the statistics sensors."""
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        entry = await _setup(hass, mocked)

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.stockroom_total_items").state == "120"
    assert hass.states.get("sensor.stockroom_total_value").state == "3456.78"
    assert hass.states.get("sensor.stockroom_rooms").state == "5"
    assert hass.states.get("sensor.stockroom_containers").state == "12"
    assert hass.states.get("sensor.stockroom_items").state == "103"


async def test_statistics_sensor_attributes(hass: HomeAssistant) -> None:
    """The total value sensor reports its measurement state class and unit."""
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        await _setup(hass, mocked)

    state = hass.states.get("sensor.stockroom_total_items")
    assert state.attributes["state_class"] == "measurement"
    assert state.attributes["unit_of_measurement"] == "items"


async def test_setup_connection_error_is_retried(hass: HomeAssistant) -> None:
    """A transport failure during first refresh marks the entry for retry."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, unique_id=MOCK_HOST)
    entry.add_to_hass(hass)
    with aioresponses() as mocked:
        mocked.get(
            URL_STATISTICS, exception=aiohttp.ClientConnectionError("boom"), repeat=True
        )
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_invalid_token_starts_reauth(hass: HomeAssistant) -> None:
    """A 401 during first refresh starts a re-authentication flow."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, unique_id=MOCK_HOST)
    entry.add_to_hass(hass)
    with aioresponses() as mocked:
        mocked.get(
            URL_STATISTICS,
            status=401,
            payload={"message": "Unauthenticated."},
            repeat=True,
        )
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)


async def test_unload_entry(hass: HomeAssistant) -> None:
    """Unloading the entry tears it down cleanly."""
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        entry = await _setup(hass, mocked)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
