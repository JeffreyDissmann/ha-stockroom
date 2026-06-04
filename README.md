# Stockroom for Home Assistant (HACS)

A custom Home Assistant integration for [Stockroom](https://github.com/JeffreyDissmann/stockroom), a self-hosted inventory manager that organises your belongings in a tree of rooms, containers, and items. It exposes inventory statistics as sensors and links Home Assistant devices to Stockroom items — with a deep link written back into Stockroom that points at the Home Assistant device page.

[![Open your Home Assistant instance and add this repository inside HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=JeffreyDissmann&repository=ha-stockroom&category=integration)
[![Open your Home Assistant instance and start setting up Stockroom](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=stockroom)

## Features

- **UI config flow** (`host`, API `token`, name) validated against `GET /api/v1/user`, with a **re-authentication** flow for rotating an expired/revoked token
- **Configurable polling interval** (default 5 minutes) via the integration options
- **Statistics sensors**: `stockroom_total_items`, `stockroom_total_value`, `stockroom_rooms`, `stockroom_containers`, `stockroom_items`
- **Device ↔ item linking** (Home Assistant device ↔ Stockroom item, strictly 1:1 and idempotent), driven entirely from the UI:
  - **Link to an existing item** — find it by **search**, by **browsing rooms**, or by **item ID** (scales to large inventories; already-linked items are filtered out)
  - **Create & link a new item** from a device, with manufacturer / model / serial **pre-filled from the Home Assistant device**
  - **Bulk create** items for all unlinked devices in an area
  - **Unlink** a device, with a safety prompt before replacing a link owned by another Home Assistant instance
- **Area ↔ room mapping** — map a Home Assistant area to a Stockroom room so new items are **auto-placed** under the right room (your inventory tree follows your HA areas)
- **Per-linked-device diagnostic sensor** exposing the linked Stockroom item ID, name, location path, and URL
- **Auto-cleanup** of the Stockroom link when the linked Home Assistant device is removed
- **Service actions** for link / unlink / create-and-link / search, for use in automations
- **English and German** translations, and a bundled **brand icon** (shown on Home Assistant 2026.3.0+)

## Requirements

- Home Assistant **2024.1.0** or newer (the bundled logo requires **2026.3.0+**)
- A reachable Stockroom instance
- A Stockroom **API token**:
  - **read** ability for the statistics sensors
  - **write** ability is additionally required for any linking / item creation

## Installation (HACS)

1. In HACS, add this repository (`JeffreyDissmann/ha-stockroom`) as a custom repository with category **Integration**, or use the badge above.
2. Install **Stockroom** from HACS and restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration → Stockroom**.

## Configuration

1. **Host** — the base URL of your Stockroom instance, e.g. `http://stockroom.local`. The integration appends `/api/v1`.
2. **API token** — a Stockroom token. On submit, the integration calls `GET /api/v1/user`; on success it confirms the connected account.
3. **Name** — the device name shown in Home Assistant (default: `Stockroom`).

To change the polling interval or manage links later, open the integration and choose **Configure**. If the token becomes invalid, Home Assistant raises a re-auth prompt; enter a new token and the existing host/settings are kept.

## Linking from the UI (recommended)

Open the integration and choose **Configure** for a menu-driven wizard:

| Menu action | What it does |
| --- | --- |
| **Link a device to an existing item** | Pick a device, then find the Stockroom item by **search**, **browse by room**, or **item ID**. |
| **Create a Stockroom item from a device and link it** | Pick a device, review details (name/type/parent + manufacturer/model/serial pre-filled from HA), then create (`POST /items`) and link. |
| **Unlink a device** | Remove the link for a chosen linked device. |
| **Bulk create from an area** | Create and link an item for every unlinked device in a Home Assistant area. |
| **Link an area to a room** | Map an HA area to a Stockroom room so new items from that area are auto-placed there. |

When linked, Stockroom stores a deep link to the HA device page, the Home Assistant device gains a diagnostic sensor showing the linked item, and the device's configuration link points back at the Stockroom item.

## Services (for automations)

The same operations are available as service actions under the `stockroom` domain:

| Service | What it does |
| --- | --- |
| `stockroom.link_item` | Links the device behind an entity to an existing Stockroom item (`PUT /items/{id}/home-assistant-link`). |
| `stockroom.unlink_item` | Removes the link for the device behind an entity (`DELETE /items/{id}/home-assistant-link`). |
| `stockroom.create_and_link_item` | Creates a new Stockroom item for an unmatched device (`POST /items`) and links it. |
| `stockroom.search` | Searches Stockroom (`GET /search?q=`) and returns the top hits with their location path. Use `response_variable` to read the results. |

Example — link the device behind an entity to Stockroom item `42`:

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

> **Note on the linked-item URL.** The per-linked-device sensor's `url` attribute (and the device's configuration link) is built as `{host}/items/{id}`. If your Stockroom web UI uses a different item path, open an issue and it can be adjusted.

## Troubleshooting

- **"needs a Stockroom token with the write ability"** — your token is read-only (HTTP 403). Create a token with the write ability for linking and item creation.
- **Invalid token / re-auth prompt** — the token was rejected (HTTP 401). Rotate it via the re-auth flow.
- **Cannot connect** — verify the host URL/port and that Stockroom is reachable from Home Assistant.
- **Rate limited (HTTP 429)** — Stockroom allows 120 requests/min per token; increase the polling interval if needed.
- **No logo on the integration card** — the bundled brand icon only renders on Home Assistant **2026.3.0+**; older cores show a placeholder.

## Development

This repo follows the Home Assistant `integration_blueprint` layout, or open it in the included **devcontainer** (VS Code → Reopen in Container).

```bash
scripts/setup     # provision both environments (see below)
scripts/develop   # run Home Assistant at http://localhost:8123 with this integration
scripts/lint      # ruff check + format check
scripts/test      # pytest (boots a real HA core with mocked Stockroom HTTP)
```

`scripts/setup` creates two environments, because the runtime and the test
harness need different Python versions:

- **`.venv` (Python 3.13)** — tests and lint, via `pytest-homeassistant-custom-component` (which pins the Home Assistant core it boots).
- **`.venv314` (Python 3.14)** — the live dev instance (`scripts/develop`), running Home Assistant 2026.3+ so local brand images work. Python 3.14.2 is fetched automatically with `uv`; no system Python 3.14 is required.

### End-to-end checklist (optional, manual)

Run only against a real Stockroom host with a **write** token:

1. `scripts/develop`, then add the integration via the UI with your host + token.
2. Confirm the statistics sensors populate and the log is error-free.
3. Use **Configure → Link a device to an existing item** (or `stockroom.link_item`); verify the link appears in Stockroom and the diagnostic sensor appears on the device.
4. Unlink the device; verify the link is removed on both sides.

## License

[MIT](LICENSE)
