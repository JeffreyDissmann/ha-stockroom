"""Tests for HA 2026.8's per-config-entry device model.

Home Assistant 2026.8 restricted a device to a single config entry and split
devices that belonged to several entries. These tests cover both halves of the
integration's answer to that: attaching the diagnostic sensor to the linked
device instead of forking a duplicate, and re-pointing stored links onto the
split devices.
"""

from __future__ import annotations

from collections.abc import Generator
import copy
from typing import Any

from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_storage

from custom_components.stockroom.const import (
    CONF_HA_DEVICE_TO_ITEM,
    CONF_ITEM_TO_HA_DEVICE,
    CONF_LINKS,
    DOMAIN,
)
from custom_components.stockroom.linking import (
    get_link_maps,
    resolve_device_id,
    resolve_device_ids,
)

from .const import (
    MOCK_CONFIG,
    MOCK_HOST,
    STATISTICS_PAYLOAD,
    URL_HA_LINK,
    URL_HA_LINKS,
    URL_MAINTENANCE_TASKS,
    URL_STATISTICS,
)

COMPOSITE_ID = "composite-device-id"
OWNER_ENTRY_ID = "owner-entry-id"
HELPER_ENTRY_ID = "helper-entry-id"
STOCKROOM_ENTRY_ID = "stockroom-entry-id"


def _ha_links(device_id: str) -> dict[str, Any]:
    """Return a GET /home-assistant-links payload linking item 42 to a device."""
    return {
        "data": [
            {
                "id": 42,
                "name": "Cordless Drill",
                "location_path": "Garage / Tool Cabinet",
                "quantity": 1,
                "home_assistant_link": {
                    "ha_device_id": device_id,
                    "friendly_name": "Cordless Drill",
                },
            }
        ],
        "meta": {"current_page": 1, "per_page": 100, "total": 1, "last_page": 1},
    }


def _linked_options(device_id: str) -> dict[str, Any]:
    """Return options linking ``device_id`` to item 42."""
    return {
        CONF_LINKS: {
            CONF_HA_DEVICE_TO_ITEM: {device_id: 42},
            CONF_ITEM_TO_HA_DEVICE: {"42": device_id},
        }
    }


_CREATED_AT = "2026-01-01T00:00:00+00:00"

# A device registry from before the 2026.8 split: the device belongs to two config
# entries - the pattern Battery Notes used to attach itself to another
# integration's device - so loading it runs core's version 3 migration and splits
# it into one device per entry.
PRE_SPLIT_DEVICE_REGISTRY = {
    "version": 1,
    "minor_version": 12,
    "key": dr.STORAGE_KEY,
    "data": {
        "devices": [
            {
                "area_id": None,
                "config_entries": [OWNER_ENTRY_ID, HELPER_ENTRY_ID],
                "config_entries_subentries": {
                    OWNER_ENTRY_ID: [None],
                    HELPER_ENTRY_ID: [None],
                },
                "configuration_url": None,
                "connections": [],
                "created_at": _CREATED_AT,
                "disabled_by": None,
                "entry_type": None,
                "hw_version": None,
                "id": COMPOSITE_ID,
                "identifiers": [["demo", "dev-1"]],
                "labels": [],
                "manufacturer": "Demo",
                "model": "Drill",
                "model_id": None,
                "modified_at": _CREATED_AT,
                "name": "Test Device",
                "name_by_user": None,
                "primary_config_entry": OWNER_ENTRY_ID,
                "serial_number": None,
                "sw_version": None,
                "via_device_id": None,
            }
        ],
        "deleted_devices": [],
    },
}


def _pre_split_registry(config_entries: list[str]) -> dict[str, Any]:
    """Return the same seed with the device shared by the given config entries."""
    seed = copy.deepcopy(PRE_SPLIT_DEVICE_REGISTRY)
    device = seed["data"]["devices"][0]
    device["config_entries"] = config_entries
    device["config_entries_subentries"] = {
        entry_id: [None] for entry_id in config_entries
    }
    return seed


# As above, but the device also belonged to this integration - what the pre-2026.8
# "copy the identifiers into DeviceInfo" pattern produced.
PRE_SPLIT_WITH_STOCKROOM_REGISTRY = _pre_split_registry(
    [OWNER_ENTRY_ID, HELPER_ENTRY_ID, STOCKROOM_ENTRY_ID]
)

# The same, but the composite never recorded a primary config entry, so no split
# is preferred and the resolution has to fall back to "anything but our own copy".
PRE_SPLIT_WITHOUT_PRIMARY_REGISTRY = _pre_split_registry(
    [OWNER_ENTRY_ID, HELPER_ENTRY_ID, STOCKROOM_ENTRY_ID]
)
PRE_SPLIT_WITHOUT_PRIMARY_REGISTRY["data"]["devices"][0]["primary_config_entry"] = None


@pytest.fixture
def seeded_storage() -> dict[str, Any]:
    """Storage contents to start Home Assistant with. Override per test."""
    return {}


@pytest.fixture
def hass_storage(seeded_storage: dict[str, Any]) -> Generator[dict[str, Any]]:
    """Override the stock fixture so a seed is in place before hass boots.

    The device registry is loaded once while the ``hass`` fixture starts up and
    cannot be reloaded afterwards, so writing to ``hass_storage`` from the test
    body is too late - the seed has to be part of the mocked store from the start.
    """
    with mock_storage(seeded_storage) as stored_data:
        yield stored_data


def _add_source_entries(hass: HomeAssistant) -> None:
    """Add the two config entries the pre-split device belonged to."""
    for entry_id, domain in ((OWNER_ENTRY_ID, "demo"), (HELPER_ENTRY_ID, "helper")):
        entry = MockConfigEntry(domain=domain, data={}, entry_id=entry_id)
        entry.add_to_hass(hass)


def _make_device(hass: HomeAssistant) -> str:
    """Create a plain device owned by another integration, with one entity."""
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


async def test_linked_item_sensor_attaches_without_forking_a_device(
    hass: HomeAssistant,
) -> None:
    """The diagnostic sensor lands on the linked device, not on a duplicate."""
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
        mocked.get(URL_HA_LINKS, payload=_ha_links(device_id), repeat=True)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    linked_entity = next(
        registry_entry
        for registry_entry in er.async_entries_for_config_entry(
            entity_registry, entry.entry_id
        )
        if registry_entry.unique_id.endswith("_linked_item")
    )
    assert linked_entity.device_id == device_id

    # Only the Stockroom hub device belongs to the entry - no duplicate of the
    # linked device was forked by copying its identifiers.
    own_devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert [device.identifiers for device in own_devices] == [
        {(DOMAIN, entry.entry_id)}
    ]


@pytest.mark.parametrize(
    "seeded_storage", [{dr.STORAGE_KEY: PRE_SPLIT_DEVICE_REGISTRY}]
)
async def test_resolve_device_id_prefers_the_owning_split(
    hass: HomeAssistant,
) -> None:
    """A composite id resolves to the split of its former primary config entry."""
    _add_source_entries(hass)

    registry = dr.async_get(hass)
    assert registry.async_is_composite_device_id(COMPOSITE_ID) is True

    resolved = resolve_device_id(hass, COMPOSITE_ID)
    assert resolved is not None
    assert resolved != COMPOSITE_ID
    assert registry.async_get(resolved).config_entry_id == OWNER_ENTRY_ID

    # Entity lookups have to span both splits, since the entities of the merged
    # device were distributed over them.
    assert set(resolve_device_ids(hass, COMPOSITE_ID)) == {
        split.id
        for split in registry.async_get_devices_for_composite_device_id(COMPOSITE_ID)
    }
    assert len(resolve_device_ids(hass, COMPOSITE_ID)) == 2


async def test_resolve_device_id_passes_through_and_reports_unknown(
    hass: HomeAssistant,
) -> None:
    """A live id is returned unchanged and an unknown id resolves to None."""
    device_id = _make_device(hass)
    assert resolve_device_id(hass, device_id) == device_id
    assert resolve_device_ids(hass, device_id) == [device_id]
    assert resolve_device_id(hass, "no-such-device") is None
    assert resolve_device_ids(hass, "no-such-device") == []


@pytest.mark.parametrize(
    "seeded_storage", [{dr.STORAGE_KEY: PRE_SPLIT_DEVICE_REGISTRY}]
)
async def test_setup_repoints_links_onto_the_split_device(
    hass: HomeAssistant,
) -> None:
    """Stored links are migrated to the split device and pushed to Stockroom."""
    _add_source_entries(hass)

    # Give the owning split an entity, so the re-pointed link has one to carry.
    owner_split_id = resolve_device_id(hass, COMPOSITE_ID)
    er.async_get(hass).async_get_or_create(
        "sensor",
        "demo",
        "unique-1",
        device_id=owner_split_id,
        config_entry=hass.config_entries.async_get_entry(OWNER_ENTRY_ID),
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG,
        unique_id=MOCK_HOST,
        options=_linked_options(COMPOSITE_ID),
    )
    entry.add_to_hass(hass)

    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=_ha_links(COMPOSITE_ID), repeat=True)
        mocked.put(URL_HA_LINK, payload={"data": {}}, repeat=True)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        put_calls = [
            call
            for (method, _url), calls in mocked.requests.items()
            if method == "PUT"
            for call in calls
        ]

    ha_device_to_item, item_to_ha_device = get_link_maps(entry)
    assert ha_device_to_item == {owner_split_id: 42}
    assert item_to_ha_device == {42: owner_split_id}

    # The new device id was pushed back to Stockroom.
    assert len(put_calls) == 1
    assert put_calls[0].kwargs["json"]["ha_device_id"] == owner_split_id


@pytest.mark.parametrize(
    "seeded_storage", [{dr.STORAGE_KEY: PRE_SPLIT_WITH_STOCKROOM_REGISTRY}]
)
async def test_setup_carries_the_sensor_over_and_drops_the_leftover_device(
    hass: HomeAssistant,
) -> None:
    """The existing sensor follows the link; the inherited device copy is dropped.

    Reproduces an upgrade from before 2026.8, where the diagnostic sensor joined
    the linked device by copying its identifiers: the device therefore also
    belonged to this integration and the split handed it a copy, and the sensor's
    unique id is still keyed on the composite device id.
    """
    _add_source_entries(hass)
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG,
        unique_id=MOCK_HOST,
        entry_id=STOCKROOM_ENTRY_ID,
        options=_linked_options(COMPOSITE_ID),
    )
    entry.add_to_hass(hass)

    owner_split_id = resolve_device_id(hass, COMPOSITE_ID)
    entity_registry.async_get_or_create(
        "sensor",
        "demo",
        "unique-1",
        device_id=owner_split_id,
        config_entry=hass.config_entries.async_get_entry(OWNER_ENTRY_ID),
    )

    # The copy the split left this integration holding, carrying the old sensor.
    leftover_device = next(
        device
        for device in dr.async_entries_for_config_entry(
            device_registry, STOCKROOM_ENTRY_ID
        )
        if device.composite_device_id == COMPOSITE_ID
    )
    existing_sensor = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_{COMPOSITE_ID}_linked_item",
        device_id=leftover_device.id,
        config_entry=entry,
    )

    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=_ha_links(COMPOSITE_ID), repeat=True)
        mocked.put(URL_HA_LINK, payload={"data": {}}, repeat=True)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # The same entity was re-keyed and moved, rather than a second one created.
    linked = [
        registry_entry
        for registry_entry in er.async_entries_for_config_entry(
            entity_registry, entry.entry_id
        )
        if registry_entry.unique_id.endswith("_linked_item")
    ]
    assert len(linked) == 1
    assert linked[0].entity_id == existing_sensor.entity_id
    assert linked[0].unique_id == f"{entry.entry_id}_{owner_split_id}_linked_item"
    assert linked[0].device_id == owner_split_id

    # The now-empty copy is gone, leaving only this integration's own hub device.
    own_devices = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    assert [device.identifiers for device in own_devices] == [
        {(DOMAIN, entry.entry_id)}
    ]


@pytest.mark.parametrize(
    "seeded_storage", [{dr.STORAGE_KEY: PRE_SPLIT_WITHOUT_PRIMARY_REGISTRY}]
)
async def test_resolve_never_picks_this_integrations_own_copy(
    hass: HomeAssistant,
) -> None:
    """With no preferred split, resolution still avoids our own copy.

    A composite with no recorded primary config entry has no split to prefer.
    Falling back to an arbitrary one can land on the copy this integration was
    handed, which would attach the sensor to a duplicate of the device.
    """
    _add_source_entries(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, data=MOCK_CONFIG, entry_id=STOCKROOM_ENTRY_ID
    )
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)

    resolved = resolve_device_id(hass, COMPOSITE_ID, STOCKROOM_ENTRY_ID)
    assert registry.async_get(resolved).config_entry_id != STOCKROOM_ENTRY_ID

    # A link already pointing at our own copy is recognised and re-resolved,
    # even though that copy is a perfectly live device id.
    own_copy = next(
        device
        for device in dr.async_entries_for_config_entry(registry, STOCKROOM_ENTRY_ID)
        if device.composite_device_id == COMPOSITE_ID
    )
    assert registry.async_is_composite_device_id(own_copy.id) is False
    assert resolve_device_id(hass, own_copy.id, STOCKROOM_ENTRY_ID) == resolved

    # Entity lookups still fan back out over every split, including our own copy -
    # an entity stranded on it has to stay findable.
    assert set(resolve_device_ids(hass, own_copy.id, STOCKROOM_ENTRY_ID)) == {
        split.id
        for split in registry.async_get_devices_for_composite_device_id(COMPOSITE_ID)
    }


@pytest.mark.parametrize(
    "seeded_storage", [{dr.STORAGE_KEY: PRE_SPLIT_WITHOUT_PRIMARY_REGISTRY}]
)
async def test_setup_repairs_a_link_left_on_our_own_copy(
    hass: HomeAssistant,
) -> None:
    """A link stranded on this integration's copy is moved to the real device."""
    _add_source_entries(hass)
    registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG,
        unique_id=MOCK_HOST,
        entry_id=STOCKROOM_ENTRY_ID,
        options={},
    )
    entry.add_to_hass(hass)

    own_copy = next(
        device
        for device in dr.async_entries_for_config_entry(registry, STOCKROOM_ENTRY_ID)
        if device.composite_device_id == COMPOSITE_ID
    )
    real_device_id = resolve_device_id(hass, COMPOSITE_ID, STOCKROOM_ENTRY_ID)
    entity_registry.async_get_or_create(
        "sensor",
        "demo",
        "unique-1",
        device_id=real_device_id,
        config_entry=hass.config_entries.async_get_entry(OWNER_ENTRY_ID),
    )
    # The state 0.4.0 could leave behind: the link points at our copy, and the
    # diagnostic sensor sits on it.
    hass.config_entries.async_update_entry(entry, options=_linked_options(own_copy.id))
    entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_{own_copy.id}_linked_item",
        device_id=own_copy.id,
        config_entry=entry,
    )

    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=_ha_links(own_copy.id), repeat=True)
        mocked.put(URL_HA_LINK, payload={"data": {}}, repeat=True)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    ha_device_to_item, _ = get_link_maps(entry)
    assert ha_device_to_item == {real_device_id: 42}

    linked = [
        registry_entry
        for registry_entry in er.async_entries_for_config_entry(
            entity_registry, entry.entry_id
        )
        if registry_entry.unique_id.endswith("_linked_item")
    ]
    assert len(linked) == 1
    assert linked[0].device_id == real_device_id

    # The duplicate is gone from the device list.
    own_devices = dr.async_entries_for_config_entry(registry, entry.entry_id)
    assert [device.identifiers for device in own_devices] == [
        {(DOMAIN, entry.entry_id)}
    ]


@pytest.mark.parametrize(
    "seeded_storage", [{dr.STORAGE_KEY: PRE_SPLIT_DEVICE_REGISTRY}]
)
async def test_service_accepts_a_pre_split_device_id(hass: HomeAssistant) -> None:
    """An automation still carrying the pre-split device id keeps working.

    Automations and scripts store the device id they were built with, and the
    2026.8 split changed it - so the service has to resolve the stored id rather
    than reject it as unlinked.
    """
    _add_source_entries(hass)
    real_device_id = resolve_device_id(hass, COMPOSITE_ID)
    er.async_get(hass).async_get_or_create(
        "sensor",
        "demo",
        "unique-1",
        device_id=real_device_id,
        config_entry=hass.config_entries.async_get_entry(OWNER_ENTRY_ID),
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG,
        unique_id=MOCK_HOST,
        options=_linked_options(real_device_id),
    )
    entry.add_to_hass(hass)

    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=_ha_links(real_device_id), repeat=True)
        mocked.get(URL_MAINTENANCE_TASKS, payload={"data": []}, repeat=True)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # COMPOSITE_ID is what a pre-upgrade automation would still pass.
        response = await hass.services.async_call(
            DOMAIN,
            "list_maintenance_tasks",
            {"device_id": COMPOSITE_ID},
            blocking=True,
            return_response=True,
        )

    assert response == {"tasks": []}
