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

# Battery sync. Push battery levels of linked items up to Stockroom on change
# and at least once a day (heartbeat), and mirror the battery type from the
# Battery Notes integration when it is installed.
BATTERY_HEARTBEAT_HOURS = 24
# Seconds between items during a manual battery re-sync. Stockroom allows 120
# requests/min per token; one item costs at least one request, so this paces the
# sweep to roughly half the budget and leaves room for polling.
BATTERY_RESYNC_INTERVAL_SECONDS = 1.0

# Battery Notes (https://codechimp.org/HA-Battery-Notes) is the de-facto source
# of battery chemistry/quantity in Home Assistant. Optional soft dependency:
# read its per-device attributes/events when present, otherwise leave the type
# alone. See manifest `after_dependencies`.
BATTERY_NOTES_DOMAIN = "battery_notes"
EVENT_BATTERY_NOTES_REPLACED = "battery_notes_battery_replaced"
ATTR_BATTERY_TYPE = "battery_type"
ATTR_BATTERY_QUANTITY = "battery_quantity"
ATTR_BATTERY_TYPE_AND_QUANTITY = "battery_type_and_quantity"

# HA attribute carrying a battery percentage on a non-battery-class entity.
ATTR_BATTERY_LEVEL = "battery_level"
