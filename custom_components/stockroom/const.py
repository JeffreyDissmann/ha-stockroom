"""Constants for the Stockroom integration."""

from datetime import timedelta

DOMAIN = "stockroom"
DEFAULT_NAME = "Stockroom"

# Config entry data keys (CONF_HOST and CONF_TOKEN come from homeassistant.const).
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"

# API.
API_BASE_PATH = "/api/v1"
DEFAULT_SCAN_INTERVAL_MINUTES = 5
MIN_SCAN_INTERVAL_MINUTES = 1
MAX_SCAN_INTERVAL_MINUTES = 1440
DEFAULT_POLL_INTERVAL = timedelta(minutes=DEFAULT_SCAN_INTERVAL_MINUTES)

# Options storage for the device <-> item link maps.
CONF_LINKS = "links"
CONF_HA_DEVICE_TO_ITEM = "ha_device_to_item"
CONF_ITEM_TO_HA_DEVICE = "item_to_ha_device"

# Services.
SERVICE_LINK_ITEM = "link_item"
SERVICE_UNLINK_ITEM = "unlink_item"
SERVICE_CREATE_AND_LINK_ITEM = "create_and_link_item"
SERVICE_SEARCH = "search"

# Service / attribute fields.
ATTR_ENTITY_ID = "entity_id"
ATTR_ITEM_ID = "item_id"
ATTR_FRIENDLY_NAME = "friendly_name"
ATTR_NAME = "name"
ATTR_TYPE = "type"
ATTR_PARENT_ID = "parent_id"
ATTR_QUERY = "query"

ITEM_TYPES = ("room", "container", "item")
DEFAULT_ITEM_TYPE = "item"
