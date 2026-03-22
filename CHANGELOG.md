# Changelog

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
