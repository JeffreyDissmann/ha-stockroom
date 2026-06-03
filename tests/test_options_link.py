"""Tests for the GUI device<->item linking wizard (options flow)."""

from __future__ import annotations

from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
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
    LINK_RESPONSE,
    MOCK_CONFIG,
    MOCK_HOST,
    STATISTICS_PAYLOAD,
    URL_HA_LINK,
    URL_ITEM,
    URL_ITEMS,
    URL_STATISTICS,
)

UNLINKED_ITEMS = {
    "data": [
        {
            "id": 42,
            "name": "Cordless Drill",
            "location_path": "Garage",
            "has_ha_link": False,
        },
        {
            "id": 43,
            "name": "Label Printer",
            "location_path": "Office",
            "has_ha_link": False,
        },
    ],
    "meta": {"current_page": 1, "per_page": 100, "total": 2, "last_page": 1},
}


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


async def _setup(
    hass: HomeAssistant, mocked: aioresponses, *, options: dict | None = None
) -> MockConfigEntry:
    mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
    mocked.get(URL_ITEM, payload=ITEM_42_PAYLOAD, repeat=True)
    entry = MockConfigEntry(
        domain=DOMAIN, data=MOCK_CONFIG, unique_id=MOCK_HOST, options=options or {}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_link_device_to_existing_item(hass: HomeAssistant) -> None:
    """The wizard links a device to a chosen existing item."""
    device_id = _make_device(hass)
    with aioresponses() as mocked:
        mocked.get(URL_ITEMS, payload=UNLINKED_ITEMS, repeat=True)
        mocked.put(URL_HA_LINK, status=201, payload=LINK_RESPONSE, repeat=True)
        entry = await _setup(hass, mocked)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.MENU

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "link_device"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "link_device"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"device_id": device_id}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "link_select_item"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"item_id": "42"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    ha_device_to_item, _ = get_link_maps(entry)
    assert ha_device_to_item == {device_id: 42}


async def test_link_device_already_linked_shows_error(hass: HomeAssistant) -> None:
    """Selecting an already-linked device shows an error on the form."""
    device_id = _make_device(hass)
    options = {
        CONF_LINKS: {
            CONF_HA_DEVICE_TO_ITEM: {device_id: 42},
            CONF_ITEM_TO_HA_DEVICE: {"42": device_id},
        }
    }
    with aioresponses() as mocked:
        entry = await _setup(hass, mocked, options=options)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "link_device"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"device_id": device_id}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "device_already_linked"}


async def test_create_and_link_via_wizard(hass: HomeAssistant) -> None:
    """The wizard creates a new item for a device and links it."""
    device_id = _make_device(hass)
    created = {"data": {"id": 77, "name": "Test Device", "type": {"value": "item"}}}
    with aioresponses() as mocked:
        # Parent dropdown options (rooms + containers) and item creation.
        empty = {"data": [], "meta": {"last_page": 1}}
        mocked.get(URL_ITEMS, payload=empty, repeat=True)
        mocked.post(URL_ITEMS, status=201, payload=created, repeat=True)
        mocked.put(URL_HA_LINK, status=201, payload=LINK_RESPONSE, repeat=True)
        entry = await _setup(hass, mocked)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "create_and_link"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"device_id": device_id}
        )
        assert result["step_id"] == "create_item_details"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"name": "Test Device", "type": "item"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    ha_device_to_item, _ = get_link_maps(entry)
    assert ha_device_to_item == {device_id: 77}


async def test_unlink_via_wizard(hass: HomeAssistant) -> None:
    """The wizard unlinks a linked device."""
    device_id = _make_device(hass)
    options = {
        CONF_LINKS: {
            CONF_HA_DEVICE_TO_ITEM: {device_id: 42},
            CONF_ITEM_TO_HA_DEVICE: {"42": device_id},
        }
    }
    with aioresponses() as mocked:
        mocked.delete(URL_HA_LINK, status=204, repeat=True)
        entry = await _setup(hass, mocked, options=options)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "unlink_device"}
        )
        assert result["step_id"] == "unlink_device"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"device_id": device_id}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    ha_device_to_item, _ = get_link_maps(entry)
    assert ha_device_to_item == {}


async def test_unlink_with_no_links_aborts(hass: HomeAssistant) -> None:
    """The unlink step aborts when nothing is linked."""
    with aioresponses() as mocked:
        entry = await _setup(hass, mocked)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "unlink_device"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_links"
