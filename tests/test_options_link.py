"""Tests for the GUI device<->item linking wizard (options flow)."""

from __future__ import annotations

from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stockroom.config_flow import _build_unique_options
from custom_components.stockroom.const import (
    CONF_AREA_LINKS,
    CONF_HA_DEVICE_TO_ITEM,
    CONF_ITEM_TO_HA_DEVICE,
    CONF_LINKS,
    DOMAIN,
)
from custom_components.stockroom.linking import get_area_room_map, get_link_maps

from .const import (
    ITEM_42_LINKED_ELSEWHERE,
    ITEM_42_PAYLOAD,
    LINK_RESPONSE,
    MOCK_CONFIG,
    MOCK_HOST,
    ROOMS_PAYLOAD,
    SEARCH_PAYLOAD,
    STATISTICS_PAYLOAD,
    URL_HA_LINK,
    URL_ITEM,
    URL_ITEMS,
    URL_ROOMS,
    URL_SEARCH,
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


def _make_device(hass: HomeAssistant, **extra) -> str:
    source_entry = MockConfigEntry(domain="demo", data={})
    source_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=source_entry.entry_id,
        identifiers={("demo", "dev-1")},
        name="Test Device",
        **extra,
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


async def _start_link(hass: HomeAssistant, entry: MockConfigEntry, device_id: str):
    """Walk to the link-method menu for a device and return the menu result."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "link_device"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"device_id": device_id}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "link_method"
    # The menu description references {device_name}; the placeholder must be set.
    assert result["description_placeholders"]["device_name"] == "Test Device"
    return result


async def test_link_via_search(hass: HomeAssistant) -> None:
    """Search by name, pick a match, and link it."""
    device_id = _make_device(hass)
    with aioresponses() as mocked:
        mocked.get(URL_SEARCH, payload=SEARCH_PAYLOAD, repeat=True)
        mocked.get(
            URL_ITEMS, payload={"data": [], "meta": {"last_page": 1}}, repeat=True
        )
        mocked.put(URL_HA_LINK, status=201, payload=LINK_RESPONSE, repeat=True)
        entry = await _setup(hass, mocked)

        result = await _start_link(hass, entry, device_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "link_search"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"query": "drill"}
        )
        assert result["step_id"] == "link_search_results"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"item_id": "42"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    ha_device_to_item, _ = get_link_maps(entry)
    assert ha_device_to_item == {device_id: 42}


async def test_link_search_no_results(hass: HomeAssistant) -> None:
    """An empty search result shows a 'no results' error."""
    device_id = _make_device(hass)
    with aioresponses() as mocked:
        mocked.get(URL_SEARCH, payload={"results": []}, repeat=True)
        mocked.get(
            URL_ITEMS, payload={"data": [], "meta": {"last_page": 1}}, repeat=True
        )
        entry = await _setup(hass, mocked)

        result = await _start_link(hass, entry, device_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "link_search"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"query": "nothing"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "link_search"
    assert result["errors"] == {"base": "no_results"}


def _select_field(result: dict, key: str):
    """Return the SelectSelector options for a field in a form result."""
    for marker, field in result["data_schema"].schema.items():
        if str(marker) == key:
            return field.config["options"]
    raise AssertionError(f"field {key} not found")


def _select_values(result: dict, key: str) -> list[str]:
    """Extract the option values of a SelectSelector field from a form result."""
    return [option["value"] for option in _select_field(result, key)]


def _select_labels(result: dict, key: str) -> list[str]:
    """Extract the option labels of a SelectSelector field from a form result."""
    return [option["label"] for option in _select_field(result, key)]


async def test_search_excludes_items_already_linked_to_ha(
    hass: HomeAssistant,
) -> None:
    """Search results omit items already linked to HA in Stockroom."""
    device_id = _make_device(hass)
    two_results = {
        "results": [
            {"id": 42, "name": "Cordless Drill", "path": "Garage"},
            {"id": 43, "name": "Label Printer", "path": "Office"},
        ]
    }
    linked = {"data": [{"id": 42, "name": "Cordless Drill"}], "meta": {"last_page": 1}}
    with aioresponses() as mocked:
        mocked.get(URL_SEARCH, payload=two_results, repeat=True)
        mocked.get(URL_ITEMS, payload=linked, repeat=True)
        entry = await _setup(hass, mocked)

        result = await _start_link(hass, entry, device_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "link_search"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"query": "drill"}
        )

    assert result["step_id"] == "link_search_results"
    # Item 42 is already linked in Stockroom, so only 43 is offered.
    assert _select_values(result, "item_id") == ["43"]


async def test_link_via_browse(hass: HomeAssistant) -> None:
    """Browse by room, pick an unlinked item, and link it."""
    device_id = _make_device(hass)
    with aioresponses() as mocked:
        mocked.get(URL_ROOMS, payload=ROOMS_PAYLOAD, repeat=True)
        mocked.get(URL_ITEMS, payload=UNLINKED_ITEMS, repeat=True)
        mocked.put(URL_HA_LINK, status=201, payload=LINK_RESPONSE, repeat=True)
        entry = await _setup(hass, mocked)

        result = await _start_link(hass, entry, device_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "link_browse"}
        )
        assert result["step_id"] == "link_browse"
        # The child room shows its own name plus the location for context.
        labels = _select_labels(result, "room_id")
        assert "Keller" in labels
        assert "KR - Regal 1 — Keller" in labels

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"room_id": "1"}
        )
        assert result["step_id"] == "link_browse_items"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"item_id": "42"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    ha_device_to_item, _ = get_link_maps(entry)
    assert ha_device_to_item == {device_id: 42}


async def test_link_via_enter_id(hass: HomeAssistant) -> None:
    """Enter an item ID directly and link it."""
    device_id = _make_device(hass)
    with aioresponses() as mocked:
        mocked.put(URL_HA_LINK, status=201, payload=LINK_RESPONSE, repeat=True)
        entry = await _setup(hass, mocked)

        result = await _start_link(hass, entry, device_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "link_enter_id"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"item_id": 42}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    ha_device_to_item, _ = get_link_maps(entry)
    assert ha_device_to_item == {device_id: 42}


async def test_link_item_linked_elsewhere_requires_confirm(hass: HomeAssistant) -> None:
    """Linking an item owned by another instance asks to confirm a replace."""
    device_id = _make_device(hass)
    with aioresponses() as mocked:
        # Safety-check fetch reports a link owned by another instance.
        mocked.get(URL_ITEM, payload=ITEM_42_LINKED_ELSEWHERE, repeat=True)
        mocked.put(URL_HA_LINK, status=200, payload=LINK_RESPONSE, repeat=True)
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        entry = MockConfigEntry(
            domain=DOMAIN, data=MOCK_CONFIG, unique_id=MOCK_HOST, options={}
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result = await _start_link(hass, entry, device_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "link_enter_id"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"item_id": 42}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "link_confirm"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"replace": True}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    ha_device_to_item, _ = get_link_maps(entry)
    assert ha_device_to_item == {device_id: 42}


async def test_link_item_linked_elsewhere_declined(hass: HomeAssistant) -> None:
    """Declining the replace aborts without changing anything."""
    device_id = _make_device(hass)
    with aioresponses() as mocked:
        mocked.get(URL_ITEM, payload=ITEM_42_LINKED_ELSEWHERE, repeat=True)
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        entry = MockConfigEntry(
            domain=DOMAIN, data=MOCK_CONFIG, unique_id=MOCK_HOST, options={}
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result = await _start_link(hass, entry, device_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "link_enter_id"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"item_id": 42}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"replace": False}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "link_not_replaced"
    ha_device_to_item, _ = get_link_maps(entry)
    assert ha_device_to_item == {}


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


async def test_create_and_link_prefills_device_details(hass: HomeAssistant) -> None:
    """Create-item pre-fills manufacturer/model/serial from the HA device."""
    device_id = _make_device(
        hass, manufacturer="Acme", model="X-1000", serial_number="SN-42"
    )
    created = {"data": {"id": 88, "name": "Acme Device", "type": {"value": "item"}}}
    with aioresponses() as mocked:
        mocked.get(
            URL_ITEMS, payload={"data": [], "meta": {"last_page": 1}}, repeat=True
        )
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
        # Submit without overriding the pre-filled detail fields.
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"name": "Acme Device", "type": "item"}
        )
        await hass.async_block_till_done()

        post_request = next(
            req
            for (method, _url), reqs in mocked.requests.items()
            for req in reqs
            if method == "POST"
        )
        body = post_request.kwargs["json"]

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert body["manufacturer"] == "Acme"
    assert body["model_number"] == "X-1000"
    assert body["serial_number"] == "SN-42"


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


async def test_link_area_sets_mapping(hass: HomeAssistant) -> None:
    """The wizard stores an HA area -> Stockroom room mapping."""
    area = ar.async_get(hass).async_get_or_create("Garage")
    with aioresponses() as mocked:
        mocked.get(URL_ROOMS, payload=ROOMS_PAYLOAD, repeat=True)
        entry = await _setup(hass, mocked)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "link_area"}
        )
        assert result["step_id"] == "link_area"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"area": area.id, "room_id": "1"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert get_area_room_map(entry) == {area.id: 1}


async def test_create_item_defaults_parent_from_area(hass: HomeAssistant) -> None:
    """Creating an item auto-places it in the room mapped to the device's area."""
    area = ar.async_get(hass).async_get_or_create("Garage")
    device_id = _make_device(hass)
    dr.async_get(hass).async_update_device(device_id, area_id=area.id)

    created = {"data": {"id": 90, "name": "Test Device", "type": {"value": "item"}}}
    rooms = {
        "data": [{"id": 1, "name": "Keller", "location_path": ""}],
        "meta": {"last_page": 1},
    }
    with aioresponses() as mocked:
        mocked.get(URL_ITEMS, payload=rooms, repeat=True)
        mocked.post(URL_ITEMS, status=201, payload=created, repeat=True)
        mocked.put(URL_HA_LINK, status=201, payload=LINK_RESPONSE, repeat=True)
        entry = await _setup(hass, mocked, options={CONF_AREA_LINKS: {area.id: 1}})

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "create_and_link"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"device_id": device_id}
        )
        # Submit without touching the parent field; it defaults to the mapped room.
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"name": "Test Device", "type": "item"}
        )
        await hass.async_block_till_done()

        post_request = next(
            req
            for (method, _url), reqs in mocked.requests.items()
            for req in reqs
            if method == "POST"
        )
        body = post_request.kwargs["json"]

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert body["parent_id"] == 1


def test_build_unique_options_dedupes_and_disambiguates() -> None:
    """Duplicate ids are dropped; shared labels are suffixed with their id."""
    options = _build_unique_options(
        [(1, "Keller"), (2, "Keller"), (2, "Keller"), (3, "Inbox")]
    )
    by_value = {option["value"]: option["label"] for option in options}
    assert by_value == {"1": "Keller (#1)", "2": "Keller (#2)", "3": "Inbox"}
