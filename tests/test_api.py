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
    ITEM_42_PAYLOAD,
    MOCK_HOST,
    MOCK_TOKEN,
    STATISTICS_PAYLOAD,
    URL_HA_LINK,
    URL_ITEM,
    URL_ITEMS,
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
