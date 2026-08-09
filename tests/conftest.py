"""Fixtures for Stockroom tests."""

from __future__ import annotations

from unittest.mock import Mock

from aiohttp.client_reqrep import ClientResponse
import aioresponses.core
import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


class _CompatClientResponse(ClientResponse):
    """ClientResponse that tolerates aioresponses' older constructor call.

    aiohttp 3.14 made ``stream_writer`` a required keyword argument, which
    aioresponses (0.7.9) does not pass yet. aioresponses fills the response body
    in by hand afterwards and never writes to the stream, so a stub is enough.
    Drop this shim once aioresponses supports aiohttp 3.14.
    """

    def __init__(self, *args, **kwargs) -> None:
        """Supply the stream writer aioresponses omits."""
        kwargs.setdefault("stream_writer", Mock())
        super().__init__(*args, **kwargs)


aioresponses.core.ClientResponse = _CompatClientResponse


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading the Stockroom custom integration in tests."""
    yield
