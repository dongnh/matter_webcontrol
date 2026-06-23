# Matter Web Controller — Agent Guide

## What is this?

A Python server for controlling Matter smart home devices (lights, sensors) and external logical bridges via REST API and MCP.

## Project structure

```
cli/
  core.py            — DeviceController: all business logic (single source of truth)
  constants.py       — magic values: Thermostat modes/clusters, sensor keys, mired bounds, extract_matter_pin
  conversions.py     — pure unit math (brightness/mired/kelvin/centi); zero deps
  serializers.py     — pure (device, names) -> dict builders + resolved_names merge
  paths.py           — resolve one data dir + every cache/storage path
  schemas.py         — Pydantic request models (POST bodies)
  auth.py            — constant-time api-key check + non-loopback bind refusal
  server.py          — FastAPI routes only (thin wrappers calling core)
  mcp_server.py      — MCP server (HTTP client calling matter-srv)
  matter_bridge.py   — Matter protocol bridge (WebSocket) + public facade + persistence
  logic_bridge.py    — Logical bridge manager (HTTP federation with remote instances)
tests/               — pytest suite; fakes.py is the single fake source (shared with dev/)
```

## Architecture rules

- **All business logic lives in `cli/core.py`** — `server.py` and `mcp_server.py` are thin wrappers only. Never duplicate logic.
- **MCP server is an HTTP client** — It calls the running `matter-srv` HTTP server. It does NOT import `core.py`, `matter_bridge.py`, or `logic_bridge.py`.
- **Device IDs are canonical** — Always `dev_*` format (e.g. `dev_a3f7c1b2`). Aliases are display-only, never used for ID resolution.
- **Physical vs logical routing** — Commands check logical bridges first, then fall back to physical Matter devices. There is exactly one router (`DeviceController._route`) and one deduped enumerator (`._iter_devices`) — route/enumerate through them, don't re-implement the traversal.
- **Bridge access goes through the facade** — `core`/`server` never touch `bridge._private` members; use the public methods on `MatterBridgeServer` (`sync`, `names_for`, `add_alias`, `device_ids_for_node`, `subscribe_occupancy`, …).
- **Mutations are POST-only** with a Pydantic body from `cli/schemas.py`; secrets go in the body, never the URL.

## Key concepts

- **Physical device** — Matter device on the LAN, controlled via WebSocket → python-matter-server
- **Logical device** — Remote device from another matter-srv instance, controlled via HTTP federation
- **Device ID** — Stable hash: `dev_{md5(hardware_unique_id + endpoint_id)[:8]}`
- **Alias** — Human-readable name for display, stored in `names_cache.json`

## How to run

```bash
# HTTP server — no sudo needed when devices are already on the LAN.
# BLE commissioning of new devices may need bluetooth group / capabilities on Linux.
export MATTER_SRV_KEY=$(openssl rand -hex 32)
matter-srv --port 8080 --fabric "My Fabric"

# MCP server (connects to running HTTP server, picks up MATTER_SRV_KEY)
matter-mcp --host localhost --port 8080
```

## Adding a new API endpoint

1. Add the business logic method to `DeviceController` in `cli/core.py` (route via `_route` / `_iter_devices`; reuse `cli/conversions.py` + `cli/serializers.py`).
2. For a mutation, add a request model to `cli/schemas.py` and a **POST** route in `cli/server.py` (`_wrap()` / `_wrap_async()` for errors); reads stay GET.
3. Add the MCP tool in `cli/mcp_server.py` — `_get()` / `_post()` to call the HTTP endpoint (POST for mutations).
4. Update `README.md`: the REST API table **and** the MCP "Available tools" list (and the `matter-mcp` flag/transport table if CLI surface changed).
5. Add a test in `tests/` and keep `dev/smoke.sh` green.
6. Update `CHANGELOG.md`.

## Conventions

- Python 3.12+, type hints everywhere
- `ruff` for linting, `black` for formatting (line-length 88)
- Exceptions: `KeyError` → 404, `ValueError` → 400, `RuntimeError` → 503
- Cache files (under `--data-dir`): `devices_cache.json`, `names_cache.json`, `bridge_cache.json`, `occupancy_cache.json`, `cache_schema.json`; fabric keys in `matter_storage/` (never commit)
- Brightness: 0–254 raw internally, 0.0–1.0 normalized in `/api/lights` and `/api/set`
- Color temperature: mireds internally, Kelvin in `/api/set`
- JSON writes are atomic (`tmp` + `os.replace`) and re-raise `OSError`

## Testing

```bash
# Unit + HTTP-edge suite (in-process, no hardware, no ports)
pip install -e ".[dev]"
ruff check cli tests dev && black --check cli tests dev && mypy cli && pytest

# Two-process federation smoke (fake bridges). A_PORT/B_PORT override the ports
# so it can run alongside a live server on 8080.
A_PORT=18080 B_PORT=18090 ./dev/start_two.sh
A_KEY=keyA B_KEY=keyB A_PORT=18080 B_PORT=18090 ./dev/smoke.sh
./dev/stop.sh
```
