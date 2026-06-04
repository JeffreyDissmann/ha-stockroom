"""Stockroom API client."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession
from homeassistant.util.network import normalize_url
from yarl import URL

from .const import API_BASE_PATH
from .models import StockroomStatistics, StockroomUser


def normalize_stockroom_host(host: str) -> str:
    """Normalize host to an absolute URL without a trailing slash."""
    candidate = host.strip()
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    return normalize_url(candidate).rstrip("/")


class StockroomApiError(Exception):
    """Base Stockroom API error."""


class StockroomAuthenticationError(StockroomApiError):
    """Stockroom token is missing or invalid (HTTP 401)."""


class StockroomPermissionError(StockroomApiError):
    """Stockroom token lacks the required ability (HTTP 403)."""


class StockroomConnectionError(StockroomApiError):
    """Connection to Stockroom failed."""


class StockroomNotFoundError(StockroomApiError):
    """Requested Stockroom resource was not found (HTTP 404)."""


class StockroomRateLimitError(StockroomApiError):
    """Stockroom rate limit exceeded (HTTP 429)."""


class StockroomValidationError(StockroomApiError):
    """Stockroom rejected the request body (HTTP 422)."""

    def __init__(self, message: str, errors: dict[str, list[str]]) -> None:
        """Store the validation message and per-field errors."""
        super().__init__(message)
        self.errors = errors


class StockroomApiClient:
    """Async client for the Stockroom v1 API."""

    def __init__(self, host: str, token: str, session: ClientSession) -> None:
        """Initialize the Stockroom API client."""
        self._host = normalize_stockroom_host(host)
        self._api_url = URL(self._host).join(URL(API_BASE_PATH.strip("/") + "/"))
        self._token = token.strip()
        self._session = session

    @property
    def host(self) -> str:
        """Return the normalized host URL."""
        return self._host

    def _build_headers(self) -> dict[str, str]:
        """Build request headers with the bearer token."""
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

    @staticmethod
    async def _extract_error_detail(response: ClientResponse) -> str:
        """Extract a short, safe detail string from an error response."""
        detail = ""
        try:
            payload = await response.json(content_type=None)
        except (ValueError, TypeError):
            payload = None

        if isinstance(payload, dict):
            value = payload.get("message")
            if isinstance(value, str) and value:
                detail = value
        elif isinstance(payload, str):
            detail = payload

        detail = " ".join(detail.split()).strip()
        if not detail:
            return "No details returned by Stockroom."
        return detail[:200]

    @staticmethod
    async def _extract_validation(
        response: ClientResponse,
    ) -> tuple[str, dict[str, list[str]]]:
        """Extract message and field errors from a 422 response."""
        message = "Validation failed."
        errors: dict[str, list[str]] = {}
        try:
            payload = await response.json(content_type=None)
        except (ValueError, TypeError):
            payload = None

        if isinstance(payload, dict):
            raw_message = payload.get("message")
            if isinstance(raw_message, str) and raw_message:
                message = raw_message
            raw_errors = payload.get("errors")
            if isinstance(raw_errors, dict):
                for field, messages in raw_errors.items():
                    if isinstance(messages, list):
                        errors[str(field)] = [str(item) for item in messages]
        return message, errors

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        error_context: str,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a request against the Stockroom API and map errors."""
        url = self._api_url.join(URL(path))

        try:
            async with self._session.request(
                method,
                url,
                headers=self._build_headers(),
                params=params,
                json=payload,
            ) as response:
                if response.status == HTTPStatus.UNAUTHORIZED:
                    raise StockroomAuthenticationError(
                        "Stockroom rejected the API token (HTTP 401)."
                    )
                if response.status == HTTPStatus.FORBIDDEN:
                    detail = await self._extract_error_detail(response)
                    raise StockroomPermissionError(
                        f"Stockroom token lacks the required ability for "
                        f"{error_context} (HTTP 403): {detail}"
                    )
                if response.status == HTTPStatus.NOT_FOUND:
                    raise StockroomNotFoundError(
                        f"Stockroom {error_context} not found (HTTP 404)."
                    )
                if response.status == HTTPStatus.UNPROCESSABLE_ENTITY:
                    message, errors = await self._extract_validation(response)
                    raise StockroomValidationError(message, errors)
                if response.status == HTTPStatus.TOO_MANY_REQUESTS:
                    raise StockroomRateLimitError(
                        "Stockroom rate limit exceeded (HTTP 429)."
                    )
                if response.status >= HTTPStatus.BAD_REQUEST:
                    detail = await self._extract_error_detail(response)
                    raise StockroomApiError(
                        f"Stockroom {error_context} failed with status "
                        f"{response.status}: {detail}"
                    )
                if response.status == HTTPStatus.NO_CONTENT:
                    return None
                try:
                    return await response.json()
                except ValueError as err:
                    raise StockroomApiError(
                        f"Stockroom {error_context} returned invalid JSON"
                    ) from err
        except ClientError as err:
            raise StockroomConnectionError from err

    # -- Read endpoints --------------------------------------------------

    async def async_get_user(self) -> StockroomUser:
        """Return the authenticated Stockroom user (``GET /user``)."""
        data = await self._async_request("GET", "user", error_context="user request")
        if not isinstance(data, dict):
            raise StockroomApiError("Stockroom user response is invalid")
        user_id = data.get("id")
        name = data.get("name")
        email = data.get("email")
        if not isinstance(user_id, int):
            raise StockroomApiError("Stockroom user response missing id")
        return StockroomUser(
            user_id=user_id,
            name=name if isinstance(name, str) and name else "Stockroom user",
            email=email if isinstance(email, str) else "",
        )

    async def async_get_statistics(self) -> StockroomStatistics:
        """Return inventory statistics (``GET /statistics``)."""
        data = await self._async_request(
            "GET", "statistics", error_context="statistics request"
        )
        if not isinstance(data, dict):
            raise StockroomApiError("Stockroom statistics response is invalid")
        return self._parse_statistics(data)

    @staticmethod
    def _parse_statistics(payload: dict[str, Any]) -> StockroomStatistics:
        """Validate and normalize a statistics payload."""
        total = payload.get("total")
        value = payload.get("value")
        by_type = payload.get("by_type")
        if not isinstance(total, int):
            raise StockroomApiError("Stockroom statistics response missing total")
        if not isinstance(value, (int, float)):
            raise StockroomApiError("Stockroom statistics response missing value")
        if not isinstance(by_type, dict):
            raise StockroomApiError("Stockroom statistics response missing by_type")

        def _count(key: str) -> int:
            raw = by_type.get(key)
            return raw if isinstance(raw, int) else 0

        return StockroomStatistics(
            total=total,
            value=float(value),
            rooms=_count("room"),
            containers=_count("container"),
            items=_count("item"),
        )

    async def async_get_items(
        self, **params: Any
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return a single page of items and its meta block (``GET /items``)."""
        query = {key: value for key, value in params.items() if value is not None}
        data = await self._async_request(
            "GET", "items", error_context="items request", params=query
        )
        if not isinstance(data, dict):
            raise StockroomApiError("Stockroom items response is invalid")
        items = data.get("data")
        meta = data.get("meta")
        if not isinstance(items, list):
            raise StockroomApiError("Stockroom items response missing data")
        return (
            [item for item in items if isinstance(item, dict)],
            meta if isinstance(meta, dict) else {},
        )

    async def async_get_all_items(self, **params: Any) -> list[dict[str, Any]]:
        """Return all items across every page for the given filters."""
        page = 1
        per_page = 100
        collected: list[dict[str, Any]] = []
        while True:
            items, meta = await self.async_get_items(
                **params, page=page, per_page=per_page
            )
            collected.extend(items)
            last_page = meta.get("last_page")
            if not items or not isinstance(last_page, int) or page >= last_page:
                break
            page += 1
        return collected

    async def async_get_ha_links(
        self, *, instance_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return all items that have a Home Assistant link.

        Calls ``GET /home-assistant-links``; each element is a full item with an
        embedded ``home_assistant_link`` object. ``instance_id`` filters to a
        single Home Assistant instance server-side.
        """
        page = 1
        per_page = 100
        collected: list[dict[str, Any]] = []
        while True:
            params: dict[str, Any] = {"page": page, "per_page": per_page}
            if instance_id is not None:
                params["instance_id"] = instance_id
            data = await self._async_request(
                "GET",
                "home-assistant-links",
                error_context="Home Assistant links request",
                params=params,
            )
            if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                raise StockroomApiError(
                    "Stockroom home-assistant-links response is invalid"
                )
            items = [item for item in data["data"] if isinstance(item, dict)]
            collected.extend(items)
            meta = data.get("meta")
            last_page = meta.get("last_page") if isinstance(meta, dict) else None
            if not items or not isinstance(last_page, int) or page >= last_page:
                break
            page += 1
        return collected

    async def async_get_item(self, item_id: int) -> dict[str, Any]:
        """Return a full item (``GET /items/{id}``)."""
        data = await self._async_request(
            "GET", f"items/{item_id}", error_context="item request"
        )
        if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
            raise StockroomApiError("Stockroom item response is invalid")
        return data["data"]

    async def async_get_rooms(self) -> list[dict[str, Any]]:
        """Return all rooms (``GET /rooms``)."""
        data = await self._async_request("GET", "rooms", error_context="rooms request")
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise StockroomApiError("Stockroom rooms response is invalid")
        return [room for room in data["data"] if isinstance(room, dict)]

    async def async_get_tags(self) -> list[dict[str, Any]]:
        """Return all tags (``GET /tags``)."""
        data = await self._async_request("GET", "tags", error_context="tags request")
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise StockroomApiError("Stockroom tags response is invalid")
        return [tag for tag in data["data"] if isinstance(tag, dict)]

    async def async_search(self, query: str) -> list[dict[str, Any]]:
        """Search for items (``GET /search?q=``)."""
        data = await self._async_request(
            "GET", "search", error_context="search request", params={"q": query}
        )
        if not isinstance(data, dict):
            raise StockroomApiError("Stockroom search response is invalid")
        results = data.get("results")
        if not isinstance(results, list):
            return []
        return [result for result in results if isinstance(result, dict)]

    # -- Write endpoints -------------------------------------------------

    async def async_create_item(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create an item (``POST /items``) and return the created item."""
        data = await self._async_request(
            "POST", "items", error_context="create item", payload=payload
        )
        if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
            raise StockroomApiError("Stockroom create item response is invalid")
        return data["data"]

    async def async_update_item(
        self, item_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Partially update an item (``PATCH /items/{id}``)."""
        data = await self._async_request(
            "PATCH",
            f"items/{item_id}",
            error_context="update item",
            payload=payload,
        )
        if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
            raise StockroomApiError("Stockroom update item response is invalid")
        return data["data"]

    async def async_set_item_ha_link(
        self,
        item_id: int,
        *,
        ha_entity_id: str,
        ha_device_id: str | None,
        friendly_name: str,
        url: str,
        instance_id: str,
    ) -> dict[str, Any]:
        """Create or replace the Home Assistant link on an item (1:1, idempotent)."""
        payload: dict[str, Any] = {
            "ha_entity_id": ha_entity_id,
            "friendly_name": friendly_name,
            "url": url,
            "instance_id": instance_id,
        }
        if ha_device_id is not None:
            payload["ha_device_id"] = ha_device_id
        data = await self._async_request(
            "PUT",
            f"items/{item_id}/home-assistant-link",
            error_context="set Home Assistant link",
            payload=payload,
        )
        if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
            raise StockroomApiError("Stockroom Home Assistant link response is invalid")
        return data["data"]

    async def async_delete_item_ha_link(self, item_id: int) -> None:
        """Remove the Home Assistant link from an item (``DELETE`` → 204)."""
        try:
            await self._async_request(
                "DELETE",
                f"items/{item_id}/home-assistant-link",
                error_context="delete Home Assistant link",
            )
        except StockroomNotFoundError:
            # The item (or its link) is already gone; treat as success.
            return

    def get_item_url(self, item_id: int) -> str:
        """Build the Stockroom web URL for a given item."""
        return f"{self._host}/items/{item_id}"
