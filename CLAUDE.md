# Matter Web Controller — Agent Guide

## What is this?

A Python server for controlling Matter smart home devices (lights, sensors) and external logical bridges via REST API and MCP.

## Project structure

```
cli/
  core.py            — DeviceController: all business logic (single source of truth)
  server.py          — FastAPI HTTP server (thin wrappers calling core)
  mcp_server.py      — MCP server (HTTP client calling matter-srv)
  matter_bridge.py   — Matter protocol bridge (WebSocket to python-matter-server)
  logic_bridge.py    — Logical bridge manager (HTTP federation with remote instances)
```

## Architecture rules

- **All business logic lives in `cli/core.py`** — `server.py` and `mcp_server.py` are thin wrappers only. Never duplicate logic.
- **MCP server is an HTTP client** — It calls the running `matter-srv` HTTP server. It does NOT import `core.py`, `matter_bridge.py`, or `logic_bridge.py`.
- **Device IDs are canonical** — Always `dev_*` format (e.g. `dev_a3f7c1b2`). Aliases are display-only, never used for ID resolution.
- **Physical vs logical routing** — Commands check logical bridges first, then fall back to physical Matter devices.

## Key concepts

- **Physical device** — Matter device on the LAN, controlled via WebSocket → python-matter-server
- **Logical device** — Remote device from another matter-srv instance, controlled via HTTP federation
- **Device ID** — Stable hash: `dev_{md5(hardware_unique_id + endpoint_id)[:8]}`
- **Alias** — Human-readable name for display, stored in `names_cache.json`

## How to run

```bash
# HTTP server (requires sudo for Matter BLE/network)
sudo matter-srv --port 8080 --fabric "My Fabric"

# MCP server (connects to running HTTP server)
matter-mcp --host localhost --port 8080
```

## Adding a new API endpoint

1. Add the business logic method to `DeviceController` in `cli/core.py`
2. Add the FastAPI route in `cli/server.py` — use `_wrap()` / `_wrap_async()` for error handling
3. Add the MCP tool in `cli/mcp_server.py` — use `_get()` / `_post()` to call the HTTP endpoint
4. Update `README.md` API Reference section
5. Update `CHANGELOG.md`

## Conventions

- Python 3.12+, type hints everywhere
- `ruff` for linting, `black` for formatting (line-length 88)
- Exceptions: `KeyError` → 404, `ValueError` → 400, `RuntimeError` → 503
- Cache files: `devices_cache.txt`, `names_cache.json`, `bridge_cache.json`, `occupancy_cache.json`
- Brightness: 0–254 raw internally, 0.0–1.0 normalized in `/api/lights` and `/api/set`
- Color temperature: mireds internally, Kelvin in `/api/set`

## Testing

No test suite yet. Manual testing:

```bash
curl http://localhost:8080/api/status
curl http://localhost:8080/api/lights
curl http://localhost:8080/api/devices
```
