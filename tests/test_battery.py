"""Tests for the Stockroom battery sync."""

from __future__ import annotations

from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stockroom.battery import _format_battery_type
from custom_components.stockroom.const import DOMAIN

from .const import (
    BATTERY_PAYLOAD,
    ITEM_42_PAYLOAD,
    MOCK_CONFIG,
    MOCK_HOST,
    STATISTICS_PAYLOAD,
    URL_BATTERY_CHANGES,
    URL_BATTERY_READINGS,
    URL_HA_LINKS,
    URL_ITEM,
    URL_STATISTICS,
)


def _ha_links(*elements: dict) -> dict:
    return {"data": list(elements), "meta": {"current_page": 1, "last_page": 1}}


def _target(
    item_id: int,
    *,
    device_id: str | None = None,
    entity_id: str | None = None,
    instance_id: str | None = None,
    battery_type: str | None = None,
) -> dict:
    link: dict = {"instance_id": instance_id}
    if device_id is not None:
        link["ha_device_id"] = device_id
    if entity_id is not None:
        link["ha_entity_id"] = entity_id
    return {"id": item_id, "battery_type": battery_type, "home_assistant_link": link}


def _make_battery_device(
    hass: HomeAssistant,
    *,
    state: str = "80",
    with_battery_notes: bool = False,
    battery_type: str | None = None,
    battery_quantity: int | None = None,
) -> tuple[str, str]:
    """Create a device with a battery sensor (and optional Battery Notes entity)."""
    source = MockConfigEntry(domain="demo", data={})
    source.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=source.entry_id,
        identifiers={("demo", "bat-1")},
        name="Drill",
    )
    battery = entity_registry.async_get_or_create(
        "sensor",
        "demo",
        "bat-pct",
        device_id=device.id,
        config_entry=source,
        original_device_class="battery",
    )
    hass.states.async_set(battery.entity_id, state, {"device_class": "battery"})
    if with_battery_notes:
        note = entity_registry.async_get_or_create(
            "sensor",
            "battery_notes",
            "bn-type",
            device_id=device.id,
            config_entry=source,
        )
        hass.states.async_set(
            note.entity_id,
            f"{battery_quantity}x {battery_type}",
            {"battery_type": battery_type, "battery_quantity": battery_quantity},
        )
    return device.id, battery.entity_id


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, unique_id=MOCK_HOST)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _posts(mocked: aioresponses, url_fragment: str) -> list[dict]:
    return [
        req.kwargs["json"]
        for (method, url), reqs in mocked.requests.items()
        for req in reqs
        if method == "POST" and url_fragment in str(url)
    ]


@pytest.mark.parametrize(
    ("battery_type", "quantity", "expected"),
    [
        ("AA", 4, "AA ×4"),
        ("CR2032", 1, "CR2032"),
        ("18650", None, "18650"),
        ("AA", 0, "AA"),
    ],
)
def test_format_battery_type(battery_type, quantity, expected) -> None:
    """Type + quantity is combined as 'AA ×4'; single/unknown omits the count."""
    assert _format_battery_type(battery_type, quantity) == expected


async def test_battery_targets_filter_by_instance(hass: HomeAssistant) -> None:
    """Targets for another HA instance are excluded; unscoped ones kept."""
    links = _ha_links(
        _target(42, device_id="dev-a", instance_id=None),
        _target(43, device_id="dev-b", instance_id="some-other-instance"),
    )
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=links, repeat=True)
        entry = await _setup(hass)

    targets = entry.runtime_data.data.battery_targets
    assert [t.item_id for t in targets] == [42]


async def test_push_on_change_and_anchor(hass: HomeAssistant) -> None:
    """An anchor reading is pushed on discovery and again on each change."""
    device_id, battery_entity = _make_battery_device(hass, state="80")
    links = _ha_links(
        _target(42, device_id=device_id, entity_id=battery_entity, battery_type="AA")
    )
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=links, repeat=True)
        mocked.post(
            URL_BATTERY_READINGS, status=201, payload=BATTERY_PAYLOAD, repeat=True
        )
        await _setup(hass)

        hass.states.async_set(battery_entity, "55.4", {"device_class": "battery"})
        await hass.async_block_till_done()

        percents = [body["percent"] for body in _posts(mocked, "battery-readings")]

    assert 80 in percents  # anchor on discovery
    assert 55 in percents  # rounded push on change


async def test_battery_type_synced_from_battery_notes(hass: HomeAssistant) -> None:
    """Battery Notes type/quantity is PATCHed onto the item as 'AA ×4'."""
    device_id, battery_entity = _make_battery_device(
        hass, with_battery_notes=True, battery_type="AA", battery_quantity=4
    )
    links = _ha_links(
        _target(42, device_id=device_id, entity_id=battery_entity, battery_type=None)
    )
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=links, repeat=True)
        mocked.post(
            URL_BATTERY_READINGS, status=201, payload=BATTERY_PAYLOAD, repeat=True
        )
        mocked.patch(URL_ITEM, payload=ITEM_42_PAYLOAD, repeat=True)
        await _setup(hass)

        patches = [
            req.kwargs["json"]
            for (method, _url), reqs in mocked.requests.items()
            for req in reqs
            if method == "PATCH"
        ]

    assert {"battery_type": "AA ×4"} in patches


async def test_battery_replaced_event_records_change(hass: HomeAssistant) -> None:
    """A Battery Notes 'replaced' event posts a battery change for the item."""
    device_id, battery_entity = _make_battery_device(hass)
    links = _ha_links(
        _target(42, device_id=device_id, entity_id=battery_entity, battery_type="AA")
    )
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=links, repeat=True)
        mocked.post(
            URL_BATTERY_READINGS, status=201, payload=BATTERY_PAYLOAD, repeat=True
        )
        mocked.post(URL_BATTERY_CHANGES, status=201, payload={"data": {}}, repeat=True)
        await _setup(hass)

        hass.bus.async_fire(
            "battery_notes_battery_replaced",
            {"device_id": device_id, "battery_type_and_quantity": "4× AA"},
        )
        await hass.async_block_till_done()

        changes = _posts(mocked, "battery-changes")

    assert len(changes) == 1
    assert "4× AA" in changes[0]["notes"]
