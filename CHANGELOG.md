# Changelog

## v0.28.0 — risk-first restructure

### Breaking Changes

- **Mutations are POST-only** — `/api/toggle`, `/api/name`, `/api/name/remove`,
  `/api/bridge`, `/api/bridge/remove`, `/api/register`, `/api/unregister`, and
  `/api/refresh` now require POST; a GET returns `405`. Secrets (peer api-key,
  pairing code) travel in the JSON body, never the URL. Any GET-based caller
  must switch to POST. MCP tools were updated to match.
- **Non-loopback bind requires a key** — binding to a non-loopback host without
  `--api-key` is now refused (was: warning). Pass `--insecure` to override.
- **`devices_cache.txt` → `devices_cache.json`** — rebuilt from Matter on first
  start, so no manual migration is needed.

### New Features

- **`GET /health`** (unauthenticated, reports `bridge_ready`) and **`GET /version`**.
- **`fan_speed` on `/api/ac` + MCP `set_ac`**; new MCP tools `unregister_node`
  and `get_metadata`.
- **`--data-dir` / `MATTER_DATA_DIR`** pins one directory for all caches and the
  Matter fabric storage; the absolute storage path is logged at startup.
- **`--log-level` / `MATTER_LOG_LEVEL`** on both `matter-srv` and `matter-mcp`.
- **Rain sensor** — a dedicated `rain` state (Matter Rain Sensor, device type
  `0x0044`) is recognised as a sensor: it surfaces in `/api/sensors` and
  `/api/metadata` with capability `rain` and `hardware_type: "rain_sensor"`
  (was: dropped). Feeds [matter-weather-sensor](https://github.com/dongnh/matter-weather-sensor)'s
  rain into light_programmer's rain override.
- **Logical-bridge occupancy SSE** — `/api/subscribe` now forwards a device's
  own occupancy stream when that device lives on a remote logical bridge,
  instead of the local Matter-fabric feed. Presence sensors hosted on logical
  bridges (e.g. [matter-mac-presence](https://github.com/dongnh/matter-mac-presence),
  [matter-appletv-presence](https://github.com/dongnh/matter-appletv-presence))
  reach subscribers again; when the upstream bridge drops, a synthetic
  `occupancy: 0` is emitted so subscribers fall back to "absent".

### Fixes

- **Self-heal logical bridges** — a bridge that was offline at startup (e.g. a
  Casambi bridge whose Bluetooth hadn't connected when the server booted) is
  retried in the background (~20 min) and registered once it appears, instead of
  being silently skipped by `load_cache()` until the next restart.

- **`set_ac` heat mode** — the setpoint is now written to the heating setpoint
  (attr 18) in Heat/EmergencyHeat and the cooling setpoint (attr 17) in
  Cool/Precooling, chosen by effective mode (was: always cooling). A single
  setpoint in Auto is rejected; the result reports a neutral `setpoint` key and
  which writes landed on partial failure.
- **`register_device`** attaches the alias to the exact device(s) of the new
  node (was: "first unnamed"); reports `name_not_applied` when none matched.
- **`fan_speed` on a physical AC** raises instead of silently succeeding.
- **Lights** report their stored brightness even when off (`state` carries on/off).
- **Aliases on logical devices** now appear consistently across endpoints; list
  endpoints and status counts agree and a federation loop can't double-count.
- **Atomic, loud cache writes** (`tmp` + `os.replace`, re-raise `OSError`) so a
  failed `set_name` no longer reports success.

### Security

- Constant-time api-key compare; SSRF guard + self-registration rejection on
  `add_bridge`; `0600` perms on `bridge_cache.json`; MCP keeps DNS-rebinding
  protection on with an allowed-hosts list; remote `HTTPError`s are translated
  to proper statuses / structured `{error}`.

### Internal / Performance

- Blocking federation calls run via `asyncio.to_thread` (a slow peer no longer
  freezes the event loop); `refresh_bridges` runs concurrently and reports
  `{refreshed, failed}`.
- `devices_cache` writes are debounced off the `ATTRIBUTE_UPDATED` hot path and
  skipped when unchanged; the ID migration runs once at startup behind a marker.
- New `cli/{constants,conversions,serializers,paths,schemas,auth}.py`; the bridge
  gained a public facade (core/server no longer touch its privates).
- pytest suite (`tests/`), CI (`ruff`/`black`/`mypy`/`pytest`), pinned deps.

## v0.27.0

### New Features

- **Logical-bridge AC control** — `/api/ac` (and the underlying `get_ac` / `set_ac` / `get_acs`) now route AC and heater devices that come from remote logical bridges, not just locally-paired Matter thermostats. Reads return the same schema as native ACs; writes are forwarded as REST calls (`LogicalBridgeClient.set_ac`). This unlocks HomeKit-only heaters (e.g. Aqara Bathroom Heater T1 via [matter-homekit-bridge](https://github.com/dongnh/matter-homekit-bridge)) as first-class thermostats.
- **`fan_speed` on `/api/ac`** — new optional field on the AC endpoint and `_ac_entry` payload, surfaced for devices whose backend bridge reports/accepts it (`0–100`). No effect on devices that don't expose a fan characteristic.
- **`system_mode` synonym in `/api/ac` payload** — accepted alongside `mode` for clarity.
- **MCP SSE/HTTP transport** — `matter-mcp` gained `--transport {stdio,sse,http}` with `--mcp-host`/`--mcp-port` for LAN access (was stdio-only). (In v0.28.0 the DNS-rebinding protection it disabled is replaced by an allowed-hosts list.)

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
