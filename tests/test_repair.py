"""Tests for the repair wizard and friendly-name sync."""

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
    URL_HA_LINKS,
    URL_ITEM,
    URL_STATISTICS,
)


def _make_device(hass: HomeAssistant, ident: str, name: str = "Device") -> str:
    source = MockConfigEntry(domain="demo", data={}, entry_id=f"src-{ident}")
    source.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=source.entry_id,
        identifiers={("demo", ident)},
        name=name,
    )
    er.async_get(hass).async_get_or_create(
        "sensor", "demo", f"uniq-{ident}", device_id=device.id, config_entry=source
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


def _links(device_id: str, item_id: int) -> dict:
    return {
        CONF_LINKS: {
            CONF_HA_DEVICE_TO_ITEM: {device_id: item_id},
            CONF_ITEM_TO_HA_DEVICE: {str(item_id): device_id},
        }
    }


async def test_friendly_name_sync_on_rename(hass: HomeAssistant) -> None:
    """Renaming a linked device pushes the new friendly name to Stockroom."""
    device_id = _make_device(hass, "dev-1", "Old Name")
    with aioresponses() as mocked:
        mocked.put(URL_HA_LINK, status=200, payload=LINK_RESPONSE, repeat=True)
        await _setup(hass, mocked, options=_links(device_id, 42))

        dr.async_get(hass).async_update_device(device_id, name_by_user="New Name")
        await hass.async_block_till_done()

        put_request = next(
            req
            for (method, _url), reqs in mocked.requests.items()
            for req in reqs
            if method == "PUT"
        )

    assert put_request.kwargs["json"]["friendly_name"] == "New Name"


async def test_repair_refresh_adopt_delete(hass: HomeAssistant) -> None:
    """Repair refreshes tracked, adopts untracked, and deletes stale links."""
    tracked = _make_device(hass, "tracked", "Tracked")
    untracked = _make_device(hass, "untracked", "Untracked")
    ha_links = {
        "data": [
            {
                "id": 10,
                "name": "Tracked item",
                "location_path": "Garage",
                "home_assistant_link": {"ha_device_id": tracked, "friendly_name": "T"},
            },
            {
                "id": 11,
                "name": "Untracked item",
                "location_path": "Office",
                "home_assistant_link": {
                    "ha_device_id": untracked,
                    "friendly_name": "U",
                },
            },
            {
                "id": 12,
                "name": "Stale item",
                "location_path": "Attic",
                "home_assistant_link": {
                    "ha_device_id": "ghost-device",
                    "friendly_name": "G",
                },
            },
        ],
        "meta": {"current_page": 1, "per_page": 100, "total": 3, "last_page": 1},
    }
    with aioresponses() as mocked:
        mocked.get(URL_HA_LINKS, payload=ha_links, repeat=True)
        mocked.put(URL_HA_LINK, status=200, payload=LINK_RESPONSE, repeat=True)
        mocked.delete(URL_HA_LINK, status=204, repeat=True)
        entry = await _setup(hass, mocked, options=_links(tracked, 10))

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "repair"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "repair"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"adopt": [untracked]}
        )
        await hass.async_block_till_done()

        delete_made = any(
            method == "DELETE" and "/items/12/" in str(url)
            for (method, url) in mocked.requests
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    device_to_item, _ = get_link_maps(entry)
    assert device_to_item == {tracked: 10, untracked: 11}
    assert delete_made


async def test_repair_nothing_to_do(hass: HomeAssistant) -> None:
    """Repair aborts when nothing needs reconciling."""
    with aioresponses() as mocked:
        mocked.get(
            URL_HA_LINKS, payload={"data": [], "meta": {"last_page": 1}}, repeat=True
        )
        entry = await _setup(hass, mocked)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "repair"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "nothing_to_repair"
