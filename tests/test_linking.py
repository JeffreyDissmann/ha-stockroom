"""Tests for the per-linked-device sensor and device-removal cleanup."""

from __future__ import annotations

from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stockroom.const import (
    CONF_HA_DEVICE_TO_ITEM,
    CONF_ITEM_TO_HA_DEVICE,
    CONF_LINKS,
    DOMAIN,
)
from custom_components.stockroom.linking import get_link_maps

from .const import (
    ITEM_42_PAYLOAD,
    MOCK_CONFIG,
    MOCK_HOST,
    STATISTICS_PAYLOAD,
    URL_HA_LINK,
    URL_ITEM,
    URL_STATISTICS,
)


def _make_device(hass: HomeAssistant) -> str:
    source_entry = MockConfigEntry(domain="demo", data={})
    source_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=source_entry.entry_id,
        identifiers={("demo", "dev-1")},
        name="Test Device",
    )
    er.async_get(hass).async_get_or_create(
        "sensor", "demo", "unique-1", device_id=device.id, config_entry=source_entry
    )
    return device.id


def _linked_options(device_id: str) -> dict:
    return {
        CONF_LINKS: {
            CONF_HA_DEVICE_TO_ITEM: {device_id: 42},
            CONF_ITEM_TO_HA_DEVICE: {"42": device_id},
        }
    }


async def test_linked_item_sensor_exposes_attributes(hass: HomeAssistant) -> None:
    """A linked device gets a diagnostic sensor with item details."""
    device_id = _make_device(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG,
        unique_id=MOCK_HOST,
        options=_linked_options(device_id),
    )
    entry.add_to_hass(hass)
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_ITEM, payload=ITEM_42_PAYLOAD, repeat=True)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = next(
        s
        for s in hass.states.async_all("sensor")
        if s.attributes.get("stockroom_item_id") == 42
    )
    assert state.state == "42"
    assert state.attributes["url"].endswith("/items/42")
    assert state.attributes["item_name"] == "Cordless Drill"
    assert state.attributes["location_path"] == "Garage / Tool Cabinet"


async def test_device_removal_cleans_up_link(hass: HomeAssistant) -> None:
    """Removing a linked HA device deletes the Stockroom link."""
    device_id = _make_device(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG,
        unique_id=MOCK_HOST,
        options=_linked_options(device_id),
    )
    entry.add_to_hass(hass)
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_ITEM, payload=ITEM_42_PAYLOAD, repeat=True)
        mocked.delete(URL_HA_LINK, status=204, repeat=True)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        dr.async_get(hass).async_remove_device(device_id)
        await hass.async_block_till_done()

    ha_device_to_item, _ = get_link_maps(entry)
    assert ha_device_to_item == {}
