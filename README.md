# Matter Web Controller

One REST and MCP surface for every Matter device on your network.

Matter Web Controller puts a fast, cached HTTP layer in front of your Matter fabric, so the rest of your home — schedulers, HomeKit bridges, dashboards, language models — can read and write devices without ever speaking the protocol. It runs as an ordinary Python service, holds the WebSocket session to `python-matter-server` for you, and federates with other instances over the same REST it exposes to clients. Python 3.12 or newer.

## Overview

A single binary, `matter-srv`, owns the Matter fabric and serves a small, considered REST API. A second binary, `matter-mcp`, mirrors that API as MCP tools for LLM clients. Both share one API key, one address scheme, and one mental model.

Devices are addressed by stable `dev_*` identifiers derived from the hardware UniqueID, so an alias change or a re-commission never breaks an existing integration. Aliases are display metadata; IDs are the contract.

Authentication is a single `X-API-Key` header — the same one used for federation between instances. No sudo. No raw sockets. Matter operational traffic is ordinary IPv6 UDP, and on macOS and most Linux setups the service runs as your user.

## How it works

The Matter bridge runs `python-matter-server` as a subprocess and connects over WebSocket. Device state is cached and event-driven, so REST responses don't block on protocol round-trips and one slow device can't stall the rest.

The logical bridge layer federates with other matter-srv instances over plain REST. Register a peer and its devices appear in your `/api/devices`; commands targeting peer-owned IDs are forwarded transparently. There is no code execution and no script payload — just one service calling another's REST.

Bluetooth commissioning is intentionally off by default. Devices are commissioned over mDNS, which is what you want once they're already on the LAN.

## Installation

Install `matter-web-controller` from PyPI into a Python 3.12+ environment, set `MATTER_SRV_KEY` to a strong random value, and launch `matter-srv`. Bind to `127.0.0.1` for local-only use, or `0.0.0.0` to expose on the LAN — the API key is required whenever the bind address is non-local. The Matter WebSocket binds to `--port + 1`.

`matter-mcp` is a thin HTTP client over the same API. Point it at a running `matter-srv` and any MCP-compatible client gets the full tool surface.

## Privileges

No root, no special groups, no capabilities for normal operation on macOS or Linux. BLE commissioning of brand-new devices on Linux is the one exception — that path needs the `bluetooth` group or matching capabilities. macOS prompts for Bluetooth access through the system dialog.

## API surface

Every endpoint requires `X-API-Key` when the server was started with one. Errors map cleanly: `404` for unknown devices, `400` for bad parameters, `401` for auth, `503` when the Matter bridge is offline, `500` for everything else.

**Status and discovery**

- `GET /api/status` — counts of lights, sensors, ACs, bridges, total devices.
- `GET /api/devices` — every physical and logical device with its full state dict.
- `GET /api/lights` — lights with normalized brightness and Kelvin.
- `GET /api/sensors`, `GET /api/sensor` — sensor list, or one by ID.
- `GET /api/climate` — temperature and humidity from every reporting device.
- `GET /api/metadata` — declarative capability descriptor used by federation peers.

**Light control**

- `POST /api/set` — high-level brightness and color temperature in one call.
- `POST /api/level`, `GET /api/level` — raw Matter level, 0–254.
- `POST /api/mired`, `GET /api/mired` — color temperature in mireds, clamped to spec.
- `GET /api/toggle` — flip on/off; also valid against ACs.
- `POST /api/batch` — multiple set actions executed in parallel.

**Air conditioners and thermostats**

- `GET /api/acs`, `GET /api/ac` — list all thermostats, or read one.
- `POST /api/ac` — write `on`, `mode`, and `setpoint` in °C; verified against Aqara Hub M200 paired with the W100 Climate Sensor acting as IR thermostat. `on=true` restores the last non-zero mode; `on=false` writes `system_mode=0`.

**Sensors**

Verified end-to-end with Aqara Motion Sensor P1, Presence Sensor FP1E, Presence Sensor FP2 (per-zone endpoints, each its own `dev_*` ID), and Presence Multi-Sensor FP300. Occupancy, illuminance, temperature, and humidity surface through `/api/sensors` and `/api/climate` as appropriate. Sensitivity, hold time, and FP2 zone layout are configured in the Aqara Home app; those are not Matter attributes and cannot be set from this server.

**Occupancy stream**

- `GET /api/subscribe` — Server-Sent Events for occupancy changes on a given `dev_*`. A keepalive comment fires every 15 seconds so dead connections are detected even when nothing is moving.

**Naming and lifecycle**

- `POST /api/name`, `GET /api/name/remove` — manage display aliases.
- `GET /api/register` — commission a Matter device by pairing code over mDNS.
- `GET /api/unregister` — drop a fabric node and clean up phantom entries.
- `GET /api/refresh` — re-pull caches from the Matter server and every logical bridge.

**Federation**

- `GET /api/bridge`, `GET /api/bridge/remove` — register or drop a remote matter-srv as a logical bridge. Its devices then participate in every read and control endpoint above.

## MCP integration

Run `matter-mcp` alongside `matter-srv` and an MCP client sees one tool per REST endpoint: device discovery, light and AC control, batch operations, alias management, commissioning, federation, and refresh. Because MCP rides on top of REST, the API key and error model are identical.

## Limitations

Only Matter devices already on the LAN are supported. Non-Matter hardware reaches the API through purpose-built adapters that speak the same REST surface — see the related projects below. One fabric per instance.

## Related projects

This service is the hub for a small constellation of single-purpose peers, all built against the REST and SSE surface above.

- **light_programmer** — circadian schedule brain and rain-driven overrides for lighting.
- **matter-weather-sensor** — exposes outdoor weather as Matter temperature, humidity, pressure, rain, and illuminance sensors.
- **yeelight_webcontrol** — brings Yeelight bulbs onto the same REST contract, including on-device color-flow effects.
- **matter-homekit-bridge** — publishes Matter devices into Apple Home with stable identities.
- **matter-homekit-ac** — HomeKit thermostat tiles backed by Matter AC writes.
- **mac-status-bridge** — Macs as HomeKit occupancy sensors via ping polling.
- **matter-mac-presence** — Macs as Matter occupancy sensors via HID idle.
- **matter-appletv-presence** — Apple TV "now playing" as a Matter occupancy sensor.

See [CLAUDE.md](CLAUDE.md) for the architecture rules followed when adding endpoints, and [CHANGELOG.md](CHANGELOG.md) for release notes.
