"""Shared constants and sample payloads for Stockroom tests."""

from __future__ import annotations

import re

from homeassistant.const import CONF_HOST, CONF_NAME, CONF_TOKEN

MOCK_HOST = "http://stockroom.local"
MOCK_TOKEN = "test-token"
MOCK_CONFIG = {
    CONF_HOST: MOCK_HOST,
    CONF_TOKEN: MOCK_TOKEN,
    CONF_NAME: "Stockroom",
}

# Regex URL matchers (aioresponses ignores query strings when matching these).
URL_USER = re.compile(r".*/api/v1/user$")
URL_STATISTICS = re.compile(r".*/api/v1/statistics$")
URL_ITEMS = re.compile(r".*/api/v1/items(\?.*)?$")
URL_ITEM = re.compile(r".*/api/v1/items/\d+$")
URL_SEARCH = re.compile(r".*/api/v1/search(\?.*)?$")
URL_HA_LINK = re.compile(r".*/api/v1/items/\d+/home-assistant-link$")

USER_PAYLOAD = {"id": 1, "name": "Jeff", "email": "jeff@example.com"}

STATISTICS_PAYLOAD = {
    "total": 120,
    "value": 3456.78,
    "by_type": {"room": 5, "container": 12, "item": 103},
    "by_tag": [],
    "by_room": [],
}

ITEM_42_PAYLOAD = {
    "data": {
        "id": 42,
        "name": "Cordless Drill",
        "type": {"value": "item", "label": "Item"},
        "location_path": "Garage / Tool Cabinet",
        "quantity": 1,
        "home_assistant_link": None,
    }
}

LINK_RESPONSE = {
    "data": {
        "ha_entity_id": "sensor.test",
        "ha_device_id": "dev-1",
        "friendly_name": "Test device",
        "url": "http://homeassistant.local/config/devices/device/dev-1",
        "instance_id": "abc",
    }
}

SEARCH_PAYLOAD = {
    "results": [
        {
            "id": 42,
            "name": "Cordless Drill",
            "type": {"value": "item", "label": "Item"},
            "path": "Garage / Tool Cabinet",
            "thumb_url": None,
        }
    ]
}
