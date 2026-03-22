# Changelog

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
