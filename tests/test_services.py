"""Tests for Stockroom service actions and the linking flow."""

from __future__ import annotations

from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stockroom.const import (
    CONF_HA_DEVICE_TO_ITEM,
    CONF_LINKS,
    DOMAIN,
    SERVICE_COMPLETE_MAINTENANCE_TASK,
    SERVICE_CREATE_AND_LINK_ITEM,
    SERVICE_CREATE_MAINTENANCE_TASK,
    SERVICE_LINK_ITEM,
    SERVICE_LIST_MAINTENANCE_TASKS,
    SERVICE_SEARCH,
    SERVICE_UNLINK_ITEM,
)
from custom_components.stockroom.linking import get_link_maps

from .const import (
    ITEM_42_PAYLOAD,
    LINK_RESPONSE,
    MAINTENANCE_TASK_PAYLOAD,
    MAINTENANCE_TASKS_PAYLOAD,
    MOCK_CONFIG,
    MOCK_HOST,
    SEARCH_PAYLOAD,
    STATISTICS_PAYLOAD,
    URL_HA_LINK,
    URL_HA_LINKS,
    URL_ITEM,
    URL_ITEMS,
    URL_MAINTENANCE_COMPLETE,
    URL_MAINTENANCE_TASKS,
    URL_SEARCH,
    URL_STATISTICS,
)

_EMPTY_HA_LINKS = {"data": [], "meta": {"current_page": 1, "last_page": 1}}


def _make_linked_device(hass: HomeAssistant) -> tuple[str, str]:
    """Create a device + entity owned by a separate config entry."""
    source_entry = MockConfigEntry(domain="demo", data={})
    source_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=source_entry.entry_id,
        identifiers={("demo", "dev-1")},
        name="Test Device",
    )
    entity_registry = er.async_get(hass)
    entity_entry = entity_registry.async_get_or_create(
        "sensor",
        "demo",
        "unique-1",
        device_id=device.id,
        config_entry=source_entry,
    )
    return device.id, entity_entry.entity_id


async def _setup_entry(
    hass: HomeAssistant, *, options: dict | None = None
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG,
        unique_id=MOCK_HOST,
        options=options or {},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_link_item_service(hass: HomeAssistant) -> None:
    """link_item writes the back-link and records the 1:1 map."""
    device_id, entity_id = _make_linked_device(hass)
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=_EMPTY_HA_LINKS, repeat=True)
        mocked.get(URL_ITEM, payload=ITEM_42_PAYLOAD, repeat=True)
        mocked.put(URL_HA_LINK, status=201, payload=LINK_RESPONSE, repeat=True)
        entry = await _setup_entry(hass)

        await hass.services.async_call(
            DOMAIN,
            SERVICE_LINK_ITEM,
            {"entity_id": entity_id, "item_id": 42},
            blocking=True,
        )
        await hass.async_block_till_done()

        ha_device_to_item, _ = get_link_maps(entry)
        assert ha_device_to_item == {device_id: 42}

        put_request = next(
            req
            for (method, _url), reqs in mocked.requests.items()
            for req in reqs
            if method == "PUT"
        )
        body = put_request.kwargs["json"]
        assert body["ha_entity_id"] == entity_id
        assert body["ha_device_id"] == device_id
        assert body["friendly_name"] == "Test Device"
        assert body["url"].endswith(f"/config/devices/device/{device_id}")
        assert "instance_id" in body


async def test_link_item_read_only_token(hass: HomeAssistant) -> None:
    """A 403 from Stockroom surfaces a clear write-ability error."""
    _, entity_id = _make_linked_device(hass)
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=_EMPTY_HA_LINKS, repeat=True)
        mocked.put(
            URL_HA_LINK,
            status=403,
            payload={"message": "This action is unauthorized."},
            repeat=True,
        )
        await _setup_entry(hass)

        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_LINK_ITEM,
                {"entity_id": entity_id, "item_id": 42},
                blocking=True,
            )
    assert err.value.translation_key == "token_read_only"


async def test_unlink_item_service(hass: HomeAssistant) -> None:
    """unlink_item removes the back-link and clears the map."""
    device_id, entity_id = _make_linked_device(hass)
    options = {
        CONF_LINKS: {
            CONF_HA_DEVICE_TO_ITEM: {device_id: 42},
            "item_to_ha_device": {"42": device_id},
        }
    }
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=_EMPTY_HA_LINKS, repeat=True)
        mocked.get(URL_ITEM, payload=ITEM_42_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=_EMPTY_HA_LINKS, repeat=True)
        mocked.delete(URL_HA_LINK, status=204, repeat=True)
        entry = await _setup_entry(hass, options=options)

        await hass.services.async_call(
            DOMAIN,
            SERVICE_UNLINK_ITEM,
            {"entity_id": entity_id},
            blocking=True,
        )
        await hass.async_block_till_done()

        ha_device_to_item, _ = get_link_maps(entry)
        assert ha_device_to_item == {}


async def test_create_and_link_item_service(hass: HomeAssistant) -> None:
    """create_and_link_item creates an item and links it."""
    device_id, entity_id = _make_linked_device(hass)
    created = {"data": {"id": 99, "name": "Test Device", "type": {"value": "item"}}}
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=_EMPTY_HA_LINKS, repeat=True)
        mocked.get(URL_ITEM, payload=ITEM_42_PAYLOAD, repeat=True)
        mocked.post(URL_ITEMS, status=201, payload=created, repeat=True)
        mocked.put(URL_HA_LINK, status=201, payload=LINK_RESPONSE, repeat=True)
        entry = await _setup_entry(hass)

        await hass.services.async_call(
            DOMAIN,
            SERVICE_CREATE_AND_LINK_ITEM,
            {"entity_id": entity_id, "type": "item"},
            blocking=True,
        )
        await hass.async_block_till_done()

        ha_device_to_item, _ = get_link_maps(entry)
        assert ha_device_to_item == {device_id: 99}

        post_request = next(
            req
            for (method, _url), reqs in mocked.requests.items()
            for req in reqs
            if method == "POST"
        )
        assert post_request.kwargs["json"] == {"name": "Test Device", "type": "item"}


async def test_search_service_returns_results(hass: HomeAssistant) -> None:
    """search returns the top hits as a service response."""
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=_EMPTY_HA_LINKS, repeat=True)
        mocked.get(URL_SEARCH, payload=SEARCH_PAYLOAD, repeat=True)
        await _setup_entry(hass)

        response = await hass.services.async_call(
            DOMAIN,
            SERVICE_SEARCH,
            {"query": "drill"},
            blocking=True,
            return_response=True,
        )

    assert response["results"][0]["name"] == "Cordless Drill"


async def test_list_maintenance_tasks_by_item_id(hass: HomeAssistant) -> None:
    """list_maintenance_tasks accepts a raw item_id and returns the tasks."""
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=_EMPTY_HA_LINKS, repeat=True)
        mocked.get(
            URL_MAINTENANCE_TASKS, payload=MAINTENANCE_TASKS_PAYLOAD, repeat=True
        )
        await _setup_entry(hass)

        response = await hass.services.async_call(
            DOMAIN,
            SERVICE_LIST_MAINTENANCE_TASKS,
            {"item_id": 42},
            blocking=True,
            return_response=True,
        )

    assert response["tasks"][0]["title"] == "Descale"


async def test_list_maintenance_tasks_by_linked_device(hass: HomeAssistant) -> None:
    """list_maintenance_tasks resolves a linked device to its item."""
    device_id, _ = _make_linked_device(hass)
    options = {
        CONF_LINKS: {
            CONF_HA_DEVICE_TO_ITEM: {device_id: 42},
            "item_to_ha_device": {"42": device_id},
        }
    }
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=_EMPTY_HA_LINKS, repeat=True)
        mocked.get(URL_HA_LINKS, payload=_EMPTY_HA_LINKS, repeat=True)
        mocked.get(
            URL_MAINTENANCE_TASKS, payload=MAINTENANCE_TASKS_PAYLOAD, repeat=True
        )
        await _setup_entry(hass, options=options)

        response = await hass.services.async_call(
            DOMAIN,
            SERVICE_LIST_MAINTENANCE_TASKS,
            {"device_id": device_id},
            blocking=True,
            return_response=True,
        )

    assert response["tasks"][0]["id"] == 7


async def test_list_maintenance_tasks_unlinked_device_errors(
    hass: HomeAssistant,
) -> None:
    """An unlinked device raises a clear device_not_linked error."""
    device_id, _ = _make_linked_device(hass)
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=_EMPTY_HA_LINKS, repeat=True)
        await _setup_entry(hass)

        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_LIST_MAINTENANCE_TASKS,
                {"device_id": device_id},
                blocking=True,
                return_response=True,
            )
    assert err.value.translation_key == "device_not_linked"


async def test_create_maintenance_task_service(hass: HomeAssistant) -> None:
    """create_maintenance_task posts a built payload and returns the task."""
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=_EMPTY_HA_LINKS, repeat=True)
        mocked.post(
            URL_MAINTENANCE_TASKS,
            status=201,
            payload=MAINTENANCE_TASK_PAYLOAD,
            repeat=True,
        )
        await _setup_entry(hass)

        response = await hass.services.async_call(
            DOMAIN,
            SERVICE_CREATE_MAINTENANCE_TASK,
            {
                "item_id": 42,
                "title": "Descale",
                "schedule_type": "interval",
                "interval_value": 3,
                "interval_unit": "months",
            },
            blocking=True,
            return_response=True,
        )

        assert response["task"]["id"] == 7
        post_request = next(
            req
            for (method, _url), reqs in mocked.requests.items()
            for req in reqs
            if method == "POST"
        )
        assert post_request.kwargs["json"] == {
            "title": "Descale",
            "schedule_type": "interval",
            "interval_value": 3,
            "interval_unit": "months",
        }


async def test_create_maintenance_task_one_off_serializes_date(
    hass: HomeAssistant,
) -> None:
    """A one-off task serializes next_due_at as an ISO date string."""
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=_EMPTY_HA_LINKS, repeat=True)
        mocked.post(
            URL_MAINTENANCE_TASKS,
            status=201,
            payload=MAINTENANCE_TASK_PAYLOAD,
            repeat=True,
        )
        await _setup_entry(hass)

        await hass.services.async_call(
            DOMAIN,
            SERVICE_CREATE_MAINTENANCE_TASK,
            {
                "item_id": 42,
                "title": "Replace battery",
                "schedule_type": "one_off",
                "next_due_at": "2026-12-01",
            },
            blocking=True,
        )

        post_request = next(
            req
            for (method, _url), reqs in mocked.requests.items()
            for req in reqs
            if method == "POST"
        )
        assert post_request.kwargs["json"]["next_due_at"] == "2026-12-01"


async def test_complete_maintenance_task_service(hass: HomeAssistant) -> None:
    """complete_maintenance_task posts to the complete endpoint by task id."""
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD, repeat=True)
        mocked.get(URL_HA_LINKS, payload=_EMPTY_HA_LINKS, repeat=True)
        mocked.post(
            URL_MAINTENANCE_COMPLETE, payload=MAINTENANCE_TASK_PAYLOAD, repeat=True
        )
        await _setup_entry(hass)

        response = await hass.services.async_call(
            DOMAIN,
            SERVICE_COMPLETE_MAINTENANCE_TASK,
            {"task_id": 7, "cost": 4.5, "notes": "Used citric acid."},
            blocking=True,
            return_response=True,
        )

        assert response["task"]["id"] == 7
        post_request = next(
            req
            for (method, _url), reqs in mocked.requests.items()
            for req in reqs
            if method == "POST"
        )
        assert post_request.kwargs["json"] == {
            "cost": 4.5,
            "notes": "Used citric acid.",
        }
