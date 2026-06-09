"""Constants for the Stockroom integration."""

DOMAIN = "stockroom"
DEFAULT_NAME = "Stockroom"

# Config entry data keys (CONF_HOST and CONF_TOKEN come from homeassistant.const).
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"

# API.
API_BASE_PATH = "/api/v1"
DEFAULT_SCAN_INTERVAL_MINUTES = 5
MIN_SCAN_INTERVAL_MINUTES = 1
MAX_SCAN_INTERVAL_MINUTES = 1440

# Options storage for the device <-> item link maps.
CONF_LINKS = "links"
CONF_HA_DEVICE_TO_ITEM = "ha_device_to_item"
CONF_ITEM_TO_HA_DEVICE = "item_to_ha_device"

# Options storage for the HA area -> Stockroom room map.
CONF_AREA_LINKS = "area_links"

# Services.
SERVICE_LINK_ITEM = "link_item"
SERVICE_UNLINK_ITEM = "unlink_item"
SERVICE_CREATE_AND_LINK_ITEM = "create_and_link_item"
SERVICE_SEARCH = "search"
SERVICE_LIST_MAINTENANCE_TASKS = "list_maintenance_tasks"
SERVICE_CREATE_MAINTENANCE_TASK = "create_maintenance_task"
SERVICE_COMPLETE_MAINTENANCE_TASK = "complete_maintenance_task"

# Service / attribute fields.
ATTR_ENTITY_ID = "entity_id"
ATTR_ITEM_ID = "item_id"
ATTR_FRIENDLY_NAME = "friendly_name"
ATTR_NAME = "name"
ATTR_TYPE = "type"
ATTR_PARENT_ID = "parent_id"
ATTR_QUERY = "query"
ATTR_MANUFACTURER = "manufacturer"
ATTR_MODEL_NUMBER = "model_number"
ATTR_SERIAL_NUMBER = "serial_number"
ATTR_DESCRIPTION = "description"

# Maintenance-task service fields.
ATTR_DEVICE_ID = "device_id"
ATTR_TASK_ID = "task_id"
ATTR_TITLE = "title"
ATTR_SCHEDULE_TYPE = "schedule_type"
ATTR_INTERVAL_VALUE = "interval_value"
ATTR_INTERVAL_UNIT = "interval_unit"
ATTR_NEXT_DUE_AT = "next_due_at"
ATTR_REMINDER_LEAD_DAYS = "reminder_lead_days"
ATTR_COMPLETED_AT = "completed_at"
ATTR_NOTES = "notes"
ATTR_COST = "cost"

# Maintenance schedule vocabulary mirrored from the Stockroom API. Only the two
# API-creatable schedule types are exposed; fixed-calendar (RRULE) schedules are
# web-only. See docs/api.md "POST /items/{item}/maintenance-tasks".
SCHEDULE_TYPE_INTERVAL = "interval"
SCHEDULE_TYPE_ONE_OFF = "one_off"
MAINTENANCE_SCHEDULE_TYPES = (SCHEDULE_TYPE_INTERVAL, SCHEDULE_TYPE_ONE_OFF)
MAINTENANCE_INTERVAL_UNITS = ("days", "weeks", "months", "years")

# Options-flow (GUI linking wizard) fields.
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_IDS = "device_ids"
CONF_AREA = "area"
CONF_ROOM_ID = "room_id"
CONF_REPLACE = "replace"

ITEM_TYPES = ("room", "container", "item")
DEFAULT_ITEM_TYPE = "item"
