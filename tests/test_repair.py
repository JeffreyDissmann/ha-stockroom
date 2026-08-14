"""Tests for the repair wizard and friendly-name sync."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stockroom import api, battery
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
    URL_BATTERY_READINGS,
    URL_HA_LINK,
    URL_HA_LINKS,
    URL_ITEM,
    URL_STATISTICS,
)


def _count_posts(mocked: aioresponses, fragment: str) -> int:
    return sum(
        len(reqs)
        for (method, url), reqs in mocked.requests.items()
        if method == "POST" and fragment in str(url)
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
        mocked.get(
            URL_HA_LINKS, payload={"data": [], "meta": {"last_page": 1}}, repeat=True
        )
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


async def test_repair_resyncs_batteries(hass: HomeAssistant) -> None:
    """Applying a repair re-pushes the battery level and re-sets the type."""
    device_id = _make_device(hass, "bat", "Battery Device")
    extra = MockConfigEntry(domain="demo", data={}, entry_id="src-bat-extra")
    extra.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    battery = entity_registry.async_get_or_create(
        "sensor",
        "demo",
        "bat-pct",
        device_id=device_id,
        config_entry=extra,
        original_device_class="battery",
    )
    hass.states.async_set(battery.entity_id, "80", {"device_class": "battery"})
    note = entity_registry.async_get_or_create(
        "sensor", "battery_notes", "bat-bn", device_id=device_id, config_entry=extra
    )
    hass.states.async_set(
        note.entity_id, "4x AA", {"battery_type": "AA", "battery_quantity": 4}
    )
    ha_links = {
        "data": [
            {
                "id": 42,
                "name": "Battery item",
                "battery_type": None,
                "home_assistant_link": {
                    "ha_device_id": device_id,
                    "friendly_name": "B",
                },
            }
        ],
        "meta": {"last_page": 1},
    }
    with aioresponses() as mocked:
        mocked.get(URL_HA_LINKS, payload=ha_links, repeat=True)
        mocked.put(URL_HA_LINK, status=200, payload=LINK_RESPONSE, repeat=True)
        mocked.post(URL_BATTERY_READINGS, status=201, payload={"data": {}}, repeat=True)
        mocked.patch(URL_ITEM, payload=ITEM_42_PAYLOAD, repeat=True)
        entry = await _setup(hass, mocked, options=_links(device_id, 42))

        readings_before = _count_posts(mocked, "battery-readings")

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "repair"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        await hass.async_block_till_done()

        readings_after = _count_posts(mocked, "battery-readings")
        patches = [
            req.kwargs["json"]
            for (method, _url), reqs in mocked.requests.items()
            for req in reqs
            if method == "PATCH"
        ]

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert readings_after > readings_before  # repair pushed a fresh reading
    assert {"battery_type": "AA ×4"} in patches


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


async def test_repair_skips_links_already_in_sync(hass: HomeAssistant) -> None:
    """A link that already matches Stockroom is not re-pushed.

    An instance with a few hundred links would otherwise walk the whole set on
    every repair, well past the per-token rate limit.
    """
    device_id = _make_device(hass, "dev-1", "Device")
    entity_id = er.async_get(hass).async_get_entity_id("sensor", "demo", "uniq-dev-1")
    ha_links = {
        "data": [
            {
                "id": 42,
                "name": "Item",
                "location_path": "Garage",
                "home_assistant_link": {
                    "ha_device_id": device_id,
                    "ha_entity_id": entity_id,
                    "friendly_name": "Device",
                },
            }
        ],
        "meta": {"last_page": 1},
    }
    with aioresponses() as mocked:
        mocked.get(URL_HA_LINKS, payload=ha_links, repeat=True)
        mocked.put(URL_HA_LINK, status=200, payload=LINK_RESPONSE, repeat=True)
        mocked.post(URL_BATTERY_READINGS, status=201, payload={}, repeat=True)
        entry = await _setup(hass, mocked, options=_links(device_id, 42))

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "repair"}
        )

        puts = [
            req
            for (method, _url), reqs in mocked.requests.items()
            for req in reqs
            if method == "PUT"
        ]

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "nothing_to_repair"
    assert puts == []


@patch.object(api, "RATE_LIMIT_BACKOFF_SECONDS", 0)
async def test_repair_keeps_progress_when_rate_limited(hass: HomeAssistant) -> None:
    """A rate limit part-way through keeps the links already repaired.

    Aborting discards the flow's result, so without this the links written to
    Stockroom would be lost on the Home Assistant side - the two ends
    disagreeing exactly when a repair was meant to fix that.
    """
    adopt_a = _make_device(hass, "a", "Adopt A")
    adopt_b = _make_device(hass, "b", "Adopt B")
    ha_links = {
        "data": [
            {
                "id": 10,
                "name": "A",
                "location_path": "",
                "home_assistant_link": {"ha_device_id": adopt_a, "friendly_name": "A"},
            },
            {
                "id": 11,
                "name": "B",
                "location_path": "",
                "home_assistant_link": {"ha_device_id": adopt_b, "friendly_name": "B"},
            },
        ],
        "meta": {"last_page": 1},
    }
    with aioresponses() as mocked:
        mocked.get(URL_HA_LINKS, payload=ha_links, repeat=True)
        # First adoption succeeds, the second is rate limited.
        mocked.put(URL_HA_LINK, status=200, payload=LINK_RESPONSE)
        mocked.put(URL_HA_LINK, status=429, repeat=True)
        entry = await _setup(hass, mocked)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "repair"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"adopt": [adopt_a, adopt_b]}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "rate_limited"

    # The link adopted before the rate limit survived on the HA side.
    ha_device_to_item, _ = get_link_maps(entry)
    assert len(ha_device_to_item) == 1


@patch.object(battery, "BATTERY_RESYNC_INTERVAL_SECONDS", 0)
async def test_repair_resync_runs_in_the_background(hass: HomeAssistant) -> None:
    """The repair dialog returns without waiting for the battery sweep.

    The sweep touches every linked item and is paced under Stockroom's rate
    limit, so awaiting it would hang the dialog for minutes on a large inventory.
    """
    device_id = _make_device(hass, "dev-1", "Device")
    entity_id = er.async_get(hass).async_get_entity_id("sensor", "demo", "uniq-dev-1")
    ha_links = {
        "data": [
            {
                "id": 42,
                "name": "Item",
                "location_path": "",
                "home_assistant_link": {
                    "ha_device_id": device_id,
                    "ha_entity_id": entity_id,
                    "friendly_name": "Device",
                },
            }
        ],
        "meta": {"last_page": 1},
    }
    with aioresponses() as mocked:
        mocked.get(URL_HA_LINKS, payload=ha_links, repeat=True)
        mocked.post(URL_BATTERY_READINGS, status=201, payload={}, repeat=True)
        entry = await _setup(hass, mocked, options=_links(device_id, 42))

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "repair"}
        )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "nothing_to_repair"

        # Backgrounded rather than awaited by the flow.
        assert entry.runtime_data.battery_sync._resync_task is not None
        await hass.async_block_till_done()


async def test_battery_resync_does_not_stack(hass: HomeAssistant) -> None:
    """Clicking repair again while a sweep runs does not start a second one."""
    with aioresponses() as mocked:
        mocked.get(
            URL_HA_LINKS, payload={"data": [], "meta": {"last_page": 1}}, repeat=True
        )
        entry = await _setup(hass, mocked)

    sync = entry.runtime_data.battery_sync
    running = asyncio.Event()

    async def _never_finishes() -> None:
        await running.wait()

    with patch.object(sync, "async_resync_now", side_effect=_never_finishes):
        sync.async_schedule_resync()
        first = sync._resync_task
        assert first is not None and not first.done()

        sync.async_schedule_resync()
        assert sync._resync_task is first

    running.set()
    await hass.async_block_till_done()
