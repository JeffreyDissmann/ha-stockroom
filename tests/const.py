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
URL_ROOMS = re.compile(r".*/api/v1/rooms(\?.*)?$")
URL_HA_LINKS = re.compile(r".*/api/v1/home-assistant-links(\?.*)?$")
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

ROOMS_PAYLOAD = {
    "data": [
        {
            "id": 1,
            "name": "Keller",
            "icon": None,
            "parent_id": None,
            "location_path": "Keller",
            "children_count": 2,
        },
        {
            "id": 2,
            "name": "KR - Regal 1",
            "icon": None,
            "parent_id": 1,
            "location_path": "Keller",
            "children_count": 0,
        },
    ]
}

ITEM_42_LINKED_ELSEWHERE = {
    "data": {
        "id": 42,
        "name": "Cordless Drill",
        "type": {"value": "item", "label": "Item"},
        "location_path": "Garage / Tool Cabinet",
        "quantity": 1,
        "home_assistant_link": {
            "ha_entity_id": "sensor.other",
            "ha_device_id": "other-device",
            "friendly_name": "Old HA device",
            "url": "http://old-ha/config/devices/device/other-device",
            "instance_id": "some-other-instance-id",
        },
    }
}
