"""Tests for the Stockroom API client."""

from __future__ import annotations

from collections.abc import AsyncIterator

import aiohttp
from aioresponses import aioresponses
import pytest

from custom_components.stockroom.api import (
    StockroomApiClient,
    StockroomAuthenticationError,
    StockroomConnectionError,
    StockroomPermissionError,
    StockroomValidationError,
)

from .const import (
    BATTERY_PAYLOAD,
    ITEM_42_PAYLOAD,
    MAINTENANCE_TASK_PAYLOAD,
    MAINTENANCE_TASKS_PAYLOAD,
    MOCK_HOST,
    MOCK_TOKEN,
    STATISTICS_PAYLOAD,
    URL_BATTERY,
    URL_BATTERY_CHANGES,
    URL_BATTERY_READINGS,
    URL_HA_LINK,
    URL_HA_LINKS,
    URL_ITEM,
    URL_ITEMS,
    URL_MAINTENANCE_COMPLETE,
    URL_MAINTENANCE_TASKS,
    URL_STATISTICS,
    URL_USER,
)


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    """Provide an aiohttp client session for direct API tests."""
    async with aiohttp.ClientSession() as client_session:
        yield client_session


def _client(session: aiohttp.ClientSession) -> StockroomApiClient:
    return StockroomApiClient(MOCK_HOST, MOCK_TOKEN, session)


async def test_get_user_sends_bearer_token(session: aiohttp.ClientSession) -> None:
    """The client sends the bearer token and parses the user."""
    with aioresponses() as mocked:
        mocked.get(URL_USER, payload={"id": 7, "name": "Ada", "email": "a@b.c"})
        user = await _client(session).async_get_user()

        assert user.user_id == 7
        assert user.name == "Ada"
        request = next(iter(mocked.requests.values()))[0]
        assert request.kwargs["headers"]["Authorization"] == f"Bearer {MOCK_TOKEN}"


async def test_get_statistics_parsed(session: aiohttp.ClientSession) -> None:
    """Statistics payload is normalized into the dataclass."""
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=STATISTICS_PAYLOAD)
        stats = await _client(session).async_get_statistics()

    assert stats.total == 120
    assert stats.value == pytest.approx(3456.78)
    assert (stats.rooms, stats.containers, stats.items) == (5, 12, 103)
    assert (stats.maintenance_overdue, stats.maintenance_due_soon) == (2, 1)


async def test_statistics_without_maintenance_defaults_to_zero(
    session: aiohttp.ClientSession,
) -> None:
    """A server without the maintenance block parses with zeroed counters."""
    payload = {
        "total": 1,
        "value": 0,
        "by_type": {"room": 0, "container": 0, "item": 1},
    }
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, payload=payload)
        stats = await _client(session).async_get_statistics()

    assert (stats.maintenance_overdue, stats.maintenance_due_soon) == (0, 0)


async def test_get_maintenance_tasks(session: aiohttp.ClientSession) -> None:
    """async_get_maintenance_tasks returns the task list for an item."""
    with aioresponses() as mocked:
        mocked.get(URL_MAINTENANCE_TASKS, payload=MAINTENANCE_TASKS_PAYLOAD)
        tasks = await _client(session).async_get_maintenance_tasks(42)

    assert [task["id"] for task in tasks] == [7]
    assert tasks[0]["is_overdue"] is True


async def test_create_maintenance_task(session: aiohttp.ClientSession) -> None:
    """async_create_maintenance_task posts the payload and unwraps the task."""
    with aioresponses() as mocked:
        mocked.post(URL_MAINTENANCE_TASKS, status=201, payload=MAINTENANCE_TASK_PAYLOAD)
        task = await _client(session).async_create_maintenance_task(
            42,
            {"title": "Descale", "schedule_type": "interval", "interval_value": 3},
        )

        assert task["id"] == 7
        request = next(iter(mocked.requests.values()))[0]
        assert request.kwargs["json"]["title"] == "Descale"


async def test_complete_maintenance_task(session: aiohttp.ClientSession) -> None:
    """async_complete_maintenance_task posts to the complete endpoint."""
    with aioresponses() as mocked:
        mocked.post(URL_MAINTENANCE_COMPLETE, payload=MAINTENANCE_TASK_PAYLOAD)
        task = await _client(session).async_complete_maintenance_task(7, {"cost": 4.5})

        assert task["id"] == 7
        request = next(iter(mocked.requests.values()))[0]
        assert request.kwargs["json"] == {"cost": 4.5}


async def test_post_battery_reading(session: aiohttp.ClientSession) -> None:
    """async_post_battery_reading sends percent (+ optional recorded_at)."""
    with aioresponses() as mocked:
        mocked.post(URL_BATTERY_READINGS, status=201, payload=BATTERY_PAYLOAD)
        result = await _client(session).async_post_battery_reading(
            42, 58, "2026-06-11T18:00:00+00:00"
        )

        assert result["current_percent"] == 60
        request = next(iter(mocked.requests.values()))[0]
        assert request.kwargs["json"] == {
            "percent": 58,
            "recorded_at": "2026-06-11T18:00:00+00:00",
        }


async def test_post_battery_reading_omits_recorded_at(
    session: aiohttp.ClientSession,
) -> None:
    """recorded_at is left out of the body when not provided."""
    with aioresponses() as mocked:
        mocked.post(URL_BATTERY_READINGS, status=201, payload=BATTERY_PAYLOAD)
        await _client(session).async_post_battery_reading(42, 58)

        request = next(iter(mocked.requests.values()))[0]
        assert request.kwargs["json"] == {"percent": 58}


async def test_post_battery_change(session: aiohttp.ClientSession) -> None:
    """async_post_battery_change posts notes to the battery-changes endpoint."""
    with aioresponses() as mocked:
        mocked.post(URL_BATTERY_CHANGES, status=201, payload={"data": {}})
        await _client(session).async_post_battery_change(42, notes="Fresh CR2032.")

        request = next(iter(mocked.requests.values()))[0]
        assert request.kwargs["json"] == {"notes": "Fresh CR2032."}


async def test_set_battery_type(session: aiohttp.ClientSession) -> None:
    """async_set_battery_type PATCHes the item with the battery_type."""
    with aioresponses() as mocked:
        mocked.patch(URL_ITEM, payload=ITEM_42_PAYLOAD)
        await _client(session).async_set_battery_type(42, "AA ×4")

        request = next(iter(mocked.requests.values()))[0]
        assert request.kwargs["json"] == {"battery_type": "AA ×4"}


async def test_get_item_battery(session: aiohttp.ClientSession) -> None:
    """async_get_item_battery unwraps the battery data object."""
    with aioresponses() as mocked:
        mocked.get(URL_BATTERY, payload=BATTERY_PAYLOAD)
        battery = await _client(session).async_get_item_battery(42)

    assert battery["battery_type"] == "CR2032"


async def test_401_raises_authentication_error(
    session: aiohttp.ClientSession,
) -> None:
    """A 401 raises a StockroomAuthenticationError."""
    with aioresponses() as mocked:
        mocked.get(URL_USER, status=401, payload={"message": "Unauthenticated."})
        with pytest.raises(StockroomAuthenticationError):
            await _client(session).async_get_user()


async def test_403_raises_permission_error(session: aiohttp.ClientSession) -> None:
    """A 403 raises a StockroomPermissionError (token lacks the ability)."""
    with aioresponses() as mocked:
        mocked.put(
            URL_HA_LINK,
            status=403,
            payload={"message": "This action is unauthorized."},
        )
        with pytest.raises(StockroomPermissionError):
            await _client(session).async_set_item_ha_link(
                42,
                ha_entity_id="sensor.test",
                ha_device_id="dev-1",
                friendly_name="Test",
                url="http://x/y",
                instance_id="abc",
            )


async def test_422_raises_validation_error_with_fields(
    session: aiohttp.ClientSession,
) -> None:
    """A 422 raises a StockroomValidationError carrying field errors."""
    with aioresponses() as mocked:
        mocked.post(
            URL_ITEMS,
            status=422,
            payload={
                "message": "The name field is required.",
                "errors": {"name": ["The name field is required."]},
            },
        )
        with pytest.raises(StockroomValidationError) as err:
            await _client(session).async_create_item({"type": "item"})

    assert "name" in err.value.errors


async def test_connection_error(session: aiohttp.ClientSession) -> None:
    """A transport error raises a StockroomConnectionError."""
    with aioresponses() as mocked:
        mocked.get(URL_STATISTICS, exception=aiohttp.ClientConnectionError("boom"))
        with pytest.raises(StockroomConnectionError):
            await _client(session).async_get_statistics()


async def test_get_all_items_paginates(session: aiohttp.ClientSession) -> None:
    """async_get_all_items follows pagination using meta.last_page."""
    with aioresponses() as mocked:
        mocked.get(
            URL_ITEMS,
            payload={
                "data": [{"id": 1, "name": "One"}],
                "meta": {
                    "current_page": 1,
                    "per_page": 100,
                    "total": 2,
                    "last_page": 2,
                },
            },
        )
        mocked.get(
            URL_ITEMS,
            payload={
                "data": [{"id": 2, "name": "Two"}],
                "meta": {
                    "current_page": 2,
                    "per_page": 100,
                    "total": 2,
                    "last_page": 2,
                },
            },
        )
        items = await _client(session).async_get_all_items(type="item")

    assert [item["id"] for item in items] == [1, 2]


async def test_delete_link_treats_404_as_success(
    session: aiohttp.ClientSession,
) -> None:
    """Deleting an already-missing link does not raise."""
    with aioresponses() as mocked:
        mocked.delete(URL_HA_LINK, status=404, payload={"message": "Not found."})
        await _client(session).async_delete_item_ha_link(99)


async def test_get_item_unwraps_data(session: aiohttp.ClientSession) -> None:
    """async_get_item returns the inner data object."""
    with aioresponses() as mocked:
        mocked.get(URL_ITEM, payload=ITEM_42_PAYLOAD)
        item = await _client(session).async_get_item(42)

    assert item["name"] == "Cordless Drill"


async def test_get_ha_links(session: aiohttp.ClientSession) -> None:
    """async_get_ha_links returns items with their embedded link object."""
    payload = {
        "data": [
            {
                "id": 42,
                "name": "Cordless Drill",
                "location_path": "Garage",
                "home_assistant_link": {
                    "ha_device_id": "dev-1",
                    "ha_entity_id": "sensor.drill",
                    "friendly_name": "Cordless Drill",
                    "instance_id": "abc",
                },
            }
        ],
        "meta": {"current_page": 1, "per_page": 100, "total": 1, "last_page": 1},
    }
    with aioresponses() as mocked:
        mocked.get(URL_HA_LINKS, payload=payload)
        links = await _client(session).async_get_ha_links()

    assert links[0]["id"] == 42
    assert links[0]["home_assistant_link"]["ha_device_id"] == "dev-1"
