# Stockroom for Home Assistant (HACS)

A custom Home Assistant integration that connects to [Stockroom](https://github.com/JeffreyDissmann), a self-hosted inventory manager. It exposes inventory statistics as sensors and links Home Assistant devices to Stockroom items (with a deep link back into Home Assistant).

[![Open your Home Assistant instance and add this repository inside HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=JeffreyDissmann&repository=ha-stockroom&category=integration)
[![Open your Home Assistant instance and start setting up Stockroom](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=stockroom)

## Features

- UI config flow (`host`, API `token`, integration name) validated against `GET /api/v1/user`
- Re-authentication flow for rotating an expired/revoked token
- Configurable polling interval (default 5 minutes) via the integration options
- Polling-based statistics sensors:
  - `stockroom_total_items`
  - `stockroom_total_value`
  - `stockroom_rooms`
  - `stockroom_containers`
  - `stockroom_items`
- Device linking workflow (Home Assistant device ↔ Stockroom item), 1:1 and idempotent
- Deep link written back into the Stockroom item, pointing at the HA device page
- Per-linked-device diagnostic sensor exposing the linked Stockroom item ID, name, location path, and URL
- Auto-cleanup of the Stockroom link when the linked HA device is removed
- Service actions for linking, unlinking, creating-and-linking, and searching (see below)

## Requirements

- Home Assistant `2024.1.0` or newer
- A reachable Stockroom instance
- A Stockroom API token
  - **read** ability for the statistics sensors
  - **write** ability is additionally required for the linking services

## Installation (HACS)

1. In HACS, add this repository (`JeffreyDissmann/ha-stockroom`) as a custom repository with category **Integration**, or use the badge above.
2. Install **Stockroom** from HACS and restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration → Stockroom**.

## Configuration

1. **Host** — the base URL of your Stockroom instance, e.g. `http://stockroom.local`. The integration appends `/api/v1`.
2. **API token** — a Stockroom token. On submit, the integration calls `GET /api/v1/user`; on success it confirms the connected account.
3. **Name** — the device name shown in Home Assistant (default: `Stockroom`).

To change the polling interval later, open the integration and choose **Configure**.

### Re-authentication

If the token becomes invalid, Home Assistant raises a re-auth prompt. Enter a new token; the existing host and settings are kept.

## The device ↔ item linking workflow

Linking ties a Home Assistant **device** to a Stockroom **item** (strictly 1:1). When linked, Stockroom stores a deep link back to the HA device page, and Home Assistant gains a diagnostic sensor on the device showing the linked item.

Services (under the `stockroom` domain):

| Service | What it does |
| --- | --- |
| `stockroom.link_item` | Links the device behind an entity to an existing Stockroom item (`PUT /items/{id}/home-assistant-link`). |
| `stockroom.unlink_item` | Removes the link for the device behind an entity (`DELETE /items/{id}/home-assistant-link`). |
| `stockroom.create_and_link_item` | Creates a new Stockroom item for an unmatched device (`POST /items`) and links it. |
| `stockroom.search` | Searches Stockroom (`GET /search?q=`) and returns the top hits with their location path. Use response variables to read the results. |

Example — link the device behind `sensor.living_room_thermostat_temperature` to Stockroom item `42`:

```yaml
action: stockroom.link_item
data:
  entity_id: sensor.living_room_thermostat_temperature
  item_id: 42
  friendly_name: Living room thermostat
```

Example — "where is X?":

```yaml
action: stockroom.search
data:
  query: drill
response_variable: hits
```

> **Note on the linked-item URL.** The per-linked-device sensor's `url` attribute (and the device's configuration link) is built as `{host}/items/{id}`. If your Stockroom web UI uses a different item path, let me know and it can be adjusted.

## Troubleshooting

- **"This action needs a Stockroom token with the write ability"** — your token is read-only (HTTP 403). Create a token with the write ability for linking.
- **Invalid token / re-auth prompt** — the token was rejected (HTTP 401). Rotate it via the re-auth flow.
- **Cannot connect** — verify the host URL/port and that Stockroom is reachable from Home Assistant.
- **Rate limited (HTTP 429)** — Stockroom allows 120 requests/min per token; increase the polling interval if needed.

## Development

This repo is based on the Home Assistant `integration_blueprint` layout.

```bash
scripts/setup     # create .venv and install HA + test dependencies
scripts/develop   # run Home Assistant at http://localhost:8123 with this integration
scripts/lint      # ruff check + format check
scripts/test      # pytest (boots a real HA core with mocked Stockroom HTTP)
```

### End-to-end checklist (optional, manual)

Only run against a real Stockroom host with a **write** token:

1. `scripts/develop`, then add the integration via the UI with your host + token.
2. Confirm the statistics sensors populate and the log is error-free.
3. Call `stockroom.link_item` for a test device and item; verify the link appears in Stockroom and the diagnostic sensor appears on the device.
4. Call `stockroom.unlink_item`; verify the link is removed on both sides.

## License

[MIT](LICENSE)
