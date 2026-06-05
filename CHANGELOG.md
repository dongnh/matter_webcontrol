# Changelog

## v0.28.0

### New Features

- **Logical-bridge occupancy over `/api/subscribe`** — the SSE endpoint is now logical-aware. When the requested device lives on a registered logical bridge, matter_webcontrol opens that bridge's own `/api/subscribe` stream and forwards its frames, so presence changes from sources like [matter-mac-presence](https://github.com/dongnh/matter-mac-presence) reach subscribers live instead of only via polling. Local Matter sensors keep their original path.

### Fixes

- **Unreachable logical bridge now reads as empty** — if a logical bridge's stream drops (for example, the Mac hosting a presence sensor goes to sleep), `/api/subscribe` emits a final `occupancy: 0` frame before closing. Subscribers fall back to "absent" instead of holding the last value, so a sleeping Mac no longer keeps a room lit.

## v0.27.0

### New Features

- **Logical-bridge AC control** — `/api/ac` (and the underlying `get_ac` / `set_ac` / `get_acs`) now route AC and heater devices that come from remote logical bridges, not just locally-paired Matter thermostats. Reads return the same schema as native ACs; writes are forwarded as REST calls (`LogicalBridgeClient.set_ac`). This unlocks HomeKit-only heaters (e.g. Aqara Bathroom Heater T1 via [matter-homekit-bridge](https://github.com/dongnh/matter-homekit-bridge)) as first-class thermostats.
- **`fan_speed` on `/api/ac`** — new optional field on the AC endpoint and `_ac_entry` payload, surfaced for devices whose backend bridge reports/accepts it (`0–100`). No effect on devices that don't expose a fan characteristic.
- **`system_mode` synonym in `/api/ac` payload** — accepted alongside `mode` for clarity.

## v0.26.1

### New Features

- **`GET /api/climate`** — unified read of temperature (°C) and humidity (%) across every reporting device, mixing thermostat `local_temperature` and standalone temp/humidity sensors. Each entry has `kind` = `"thermostat"` or `"sensor"`. `?id=…` returns one device. New MCP tool: `get_climate`.

## v0.26.0

### New Features

- **Native Thermostat / AC control** — Matter Thermostat endpoints (cluster 513) are now first-class. `MatterBridgeServer._update_cache` extracts `local_temperature`, `system_mode`, `cooling_setpoint`, `heating_setpoint`. New endpoints: `GET /api/acs`, `GET/POST /api/ac` (read/control: `on`, `mode`, `setpoint` in °C). New MCP tools: `list_acs`, `get_ac`, `set_ac`. `/api/toggle` and `/api/set` (brightness 0/1) on a thermostat ID map to on/off via `system_mode`. `/api/status` adds `acs_on` / `acs_off` counters; `/api/metadata` recognises `hardware_type: "thermostat"`. Verified on Aqara Hub M200 + Climate Sensor W100 — Aqara hubs expose paired IR ACs through this path (no Scenes cluster is exposed, so the earlier scene-recall design from v0.26.0-dev was dropped).

## v0.25.1

### Improvements

- **Auto-dedupe on re-pair** — after a `commission_with_code` succeeds, any older fabric node sharing the new node's endpoint-0 UniqueID/SerialNumber is automatically unpaired. Eliminates duplicate `dev_*` entries when re-commissioning Aqara/Eve hubs that keep their UniqueID across pairings.
- **`GET /api/unregister?node_id=N`** — manual fabric-node unpair for cleaning up phantom entries.

## v0.25.0

### Breaking Changes

- **Federation no longer uses embedded scripts** — `/api/metadata` no longer returns Python `events.{name}.script` blobs. Peers now call the standard REST API (`/api/devices`, `/api/level`, `/api/mired`, `/api/set`) directly. Closes a remote-code-execution risk where a malicious or MITM'd peer could inject arbitrary Python via `exec()`.
- **Default bind address is `127.0.0.1`** — previously `0.0.0.0`. Pass `--host 0.0.0.0` to expose on the LAN; doing so without `--api-key` now logs a warning.
- **`/api/metadata` schema** — emits `capabilities` and `states` per device instead of `events`. New `bridge.api_version: "2"` marker.
- **`register_device` response** — returns `assigned_id` (the dev_* the alias was applied to) instead of `pending_name`.

### New Features

- **API authentication** — `--api-key` flag (or `MATTER_SRV_KEY` env var) requires `X-API-Key` header on all requests. MCP server and federation client both forward the header automatically.
- **Federation auth pass-through** — `/api/bridge?ip=&port=&api_key=` and `add_bridge` MCP tool accept the remote's API key, stored in `bridge_cache.json`.
- **SSE keepalive** — `/api/subscribe` emits a comment frame every 15s, so client disconnects are detected even when no occupancy events fire.

### Improvements

- **Logical-first routing** — `_find_state`, `get_sensor` now check logical bridges before physical (matches architecture rule). Previously inconsistent.
- **Parallel batch** — `/api/batch` now dispatches via `asyncio.gather` instead of sequential await.
- **Mireds clamping** — color temperature requests clamped to Matter spec range [153, 500].
- **`get_status` dedup** — devices appearing on both physical and logical (federation loop) counted once.
- **Logical bridge cache** — `LogicalBridgeClient.refresh()` pulls once; `get_all_devices()` reads from memory. Eliminates per-call HTTP fan-out.
- **Atomic + resilient cache** — `bridge_cache.json` written via tmp + `os.replace`; corrupt file no longer crashes startup.
- **Error semantics** — `_parse_id` now raises `KeyError` (404) for "device not found" instead of `ValueError` (400).

## v0.24.0

### New Features

- **MCP server** — New `matter-mcp` command connects to a running HTTP server and exposes all device operations as MCP tools for LLM integration
- **Toggle** — `GET /api/toggle?id=` to flip device on/off without specifying brightness
- **Batch control** — `POST /api/batch` to control multiple devices in one request
- **Status summary** — `GET /api/status` returns quick counts (lights on/off, sensors, bridges)
- **Remove alias** — `GET /api/name/remove?id=&name=` to delete a device alias
- **Remove bridge** — `GET /api/bridge/remove?ip=&port=` to unregister a logical bridge

### Breaking Changes

- **Aliases are display-only** — All API endpoints now require the canonical `dev_*` device ID. Aliases set via `/api/name` are no longer resolved as IDs.

### Architecture

- **Shared core** — Extracted all business logic into `cli/core.py` (`DeviceController` class). Both `server.py` (FastAPI) and `mcp_server.py` (MCP) are thin wrappers with zero duplicated logic.
- **MCP as HTTP client** — `matter-mcp` connects to a running `matter-srv` via HTTP instead of initializing its own Matter bridge. Supports `--host` and `--port` options.

## v0.23.0

### Breaking Changes

- **Stable device IDs** — Physical Matter devices now use hash-based IDs derived from hardware UniqueID (e.g. `dev_a3f7c1b2`) instead of `dev_{node_id}_{endpoint_id}`. Existing cache keys (names, occupancy history) are auto-migrated on first run. External systems referencing old-format IDs will need to update — alias-based references are unaffected.

### New Features

- **`--fabric` option** — Set the Matter fabric label at startup: `matter-srv --fabric "Home Lab"`

### Improvements

- **Codebase refactor** — Extracted shared helpers to reduce code duplication across all source files:
  - Unified GET/POST parameter parsing (`_get_params`)
  - Shared device lookup functions (`_find_device_state`, `_find_logical_target`)
  - Reusable light/sensor payload builders (`_build_light_entry`, `_build_sensor_entry`)
  - Generic JSON cache helpers (`_load_json`, `_save_json`) replacing 6 separate methods
- **Logical bridge client** — Stores `host`/`port` directly instead of parsing from URL string
- **README rewrite** — Cleaner language, CLI options table, parameter tables for all endpoints, collapsible metadata example, `sudo` usage note

## v0.22.0

- Add `/api/level` and `/api/mired` endpoints for raw brightness and color temperature control
- Add `/api/metadata` endpoint for bridge federation with embedded Python scripts
- Add `/api/refresh` endpoint to force-refresh all device states
- Combine `color_temp_mireds` and `color_temperature` into unified data format

## v0.21.0

- Add bridge refresh and startup synchronization
- Add `/api/refresh` endpoint

## v0.20.0

- Add color temperature support
