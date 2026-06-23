# Matter Web Controller

A fast REST + MCP API in front of your Matter home.

## Overview

Matter Web Controller is the hub of a small Matter home. Schedulers, HomeKit bridges, dashboards, presence sensors, and LLMs all talk to this one service — and none of them have to speak Matter. It runs [`python-matter-server`](https://github.com/home-assistant-libs/python-matter-server) as a subprocess and holds the WebSocket session; state is cached and event-driven, so a read never blocks on a protocol round-trip.

Several instances federate over the same REST they serve. Register a peer and its devices appear here, with commands forwarded automatically — no embedded code, just HTTP.

It runs as your user. Matter operational traffic is ordinary IPv6 UDP, so there are no raw sockets and no `sudo` on macOS or most Linux. It is built for home use. It is not hardened for commercial deployment.

## How it works

A single key, the `X-API-Key` header, authenticates every request — and federation between instances. `GET /health` and `GET /version` are the only open endpoints. Secrets like a peer's key or a pairing code travel in the JSON body, never the URL.

Devices are addressed by stable hash IDs derived from hardware UniqueID, like `dev_a3f7c1b2`. Aliases you set with `/api/name` are display-only and are not accepted as IDs.

## Quick start

```bash
pip install matter-web-controller

export MATTER_SRV_KEY=$(openssl rand -hex 32)
matter-srv --fabric "My Home"

# In another terminal
curl -H "X-API-Key: $MATTER_SRV_KEY" http://127.0.0.1:8080/api/status

# Pair a device (code from its app, e.g. Aqara Home)
curl -H "X-API-Key: $MATTER_SRV_KEY" -H "Content-Type: application/json" \
  -d '{"code":"2456-515-1552","name":"Living Room"}' \
  http://127.0.0.1:8080/api/register
```

Requires Python 3.12+. The Matter WebSocket binds to the REST port + 1.

`matter-srv` flags: `--port` (8080), `--host` (127.0.0.1; `0.0.0.0` for LAN), `--api-key` / `$MATTER_SRV_KEY`, `--fabric`, `--data-dir` / `$MATTER_DATA_DIR` (caches + fabric storage; absolute path logged at startup), `--insecure`, `--log-level` / `$MATTER_LOG_LEVEL`. A non-loopback bind without a key is refused unless `--insecure`.

## REST API

Errors map cleanly: `404` unknown device, `400` bad params, `401` auth, `405` a POST-only endpoint called with GET, `503` Matter bridge offline, `500` other.

**Read** (GET)
- `/api/status` — counts: lights on/off, sensors active, ACs on/off, bridges, total devices
- `/api/devices` — raw states for every device
- `/api/lights` — normalized brightness 0–1 and Kelvin; brightness is kept even when off, `state` carries on/off
- `/api/sensors`, `/api/sensor?id` — all sensors, or one
- `/api/climate[?id]` — temperature (°C) and humidity (%)
- `/api/level?id`, `/api/mired?id` — read raw brightness (0–254) or color temperature (mireds)
- `/api/metadata` — declarative capabilities, used by federation peers
- `/health`, `/version` — unauthenticated

**Control** (POST)
- `/api/set` `{id, brightness 0–1, temperature Kelvin}` — both optional
- `/api/toggle` `{id}` — flip on/off; also valid on ACs
- `/api/level` `{id, level 0–254}`, `/api/mired` `{id, mireds 153–500}` (clamped)
- `/api/batch` `{actions:[…]}` — run in parallel

```bash
curl -H "X-API-Key: $MATTER_SRV_KEY" -H "Content-Type: application/json" \
  -d '{"id":"dev_e3798593","brightness":0.8,"temperature":2700}' \
  http://127.0.0.1:8080/api/set
```

**AC / Thermostat**
- `GET /api/acs`, `GET /api/ac?id` — list, or read one
- `POST /api/ac` `{id, on, mode, setpoint °C, fan_speed}`

  `system_mode`: 0 Off, 1 Auto, 3 Cool, 4 Heat, 5 EmergencyHeat, 6 Precooling, 7 FanOnly, 8 Dry, 9 Sleep. `on=true` resumes the last non-zero mode (default Cool); `on=false` writes mode 0; `mode` overrides `on` (`system_mode` is a synonym). The setpoint routes to the heating setpoint in Heat/EmergencyHeat or the cooling setpoint in Cool/Precooling; a lone setpoint in Auto is rejected. `fan_speed` (0–100) applies only to logical-bridge ACs that report it, and is rejected on physical Matter ACs.

**Management** (POST)
- `/api/name` `{id, name}`, `/api/name/remove` `{id, name}`
- `/api/register` `{code, name, ip}` — commission by pairing code
- `/api/unregister` `{node_id}`, `/api/refresh`

**Federation** (POST)
- `/api/bridge` `{ip, port, api_key}` — register a remote matter-srv (key in body)
- `/api/bridge/remove` `{ip, port}`

  Only private/loopback IPs or LAN hostnames are accepted, and self-registration is rejected.

**SSE** — `GET /api/subscribe?id` streams occupancy as Server-Sent Events with a `: keepalive` comment every 15s. For a device on a remote logical bridge, it forwards that bridge's own stream; if the upstream drops, a synthetic `occupancy:0` frame lets subscribers fall back to absent.

```bash
curl -N -H "X-API-Key: $MATTER_SRV_KEY" \
  "http://127.0.0.1:8080/api/subscribe?id=dev_b503384e"
# data: {"id":"dev_b503384e","occupancy":1,"timestamp":"…"}
# : keepalive   ← every 15s
```

## MCP

`matter-mcp` is a thin HTTP client over the same REST API — same key, same error model. About 22 tools mirror the endpoints: `get_devices`, `get_lights`, `get_sensors`, `get_sensor`, `get_climate`, `get_status`, `set_device`, `toggle`, `set_level`, `set_mired`, `batch_control`, `set_name`, `remove_name`, `add_bridge`, `remove_bridge`, `register_device`, `unregister_node`, `list_acs`, `get_ac`, `set_ac`, `get_metadata`, `refresh`. The SSE stream is HTTP-only.

Flags: `--host` (localhost), `--port` (8080), `--api-key`, `--transport` (stdio | sse | http; stdio for local agents), `--mcp-host` (127.0.0.1), `--mcp-port` (7861), `--log-level`.

```json
{
  "mcpServers": {
    "matter": {
      "command": "matter-mcp",
      "args": ["--host", "localhost", "--port", "8080"],
      "env": { "MATTER_SRV_KEY": "your-key-here" }
    }
  }
}
```

## Verified hardware

Verified with the Aqara Hub M200 bridging Aqara sensors over Matter — Motion Sensor P1 (PIR + lux), Presence FP1E (mmWave single-zone), Presence FP2 (mmWave multi-zone, each zone its own `dev_*` ID), and Presence Multi-Sensor FP300 (mmWave + lux + temp/humidity) — plus the Aqara Climate Sensor W100 paired to an IR AC and bridged as a Matter Thermostat (cluster 0x0201), where writes become IR commands. Sensitivity, hold times, and FP2 zones are configured in the Aqara app, not over Matter, and Aqara does not bridge in-app Scenes. A Matter Rain Sensor (device type 0x0044) surfaces as capability `rain`.

## Limitations

- Only Matter devices already on the LAN. Non-Matter hardware (Casambi, Yeelight, and the like) needs an adapter that exposes this same REST surface.
- BLE commissioning is off by default in `python-matter-server` builds; devices commission over mDNS.
- One fabric per instance.

## Related projects

This service is the hub of a small constellation, all at [github.com/dongnh](https://github.com/dongnh):

- [light_programmer](https://github.com/dongnh/light_programmer) — circadian schedule brain with a rain override
- [matter-weather-sensor](https://github.com/dongnh/matter-weather-sensor) — weather as Matter temp/humidity/pressure/rain/illuminance sensors
- [yeelight_webcontrol](https://github.com/dongnh/yeelight_webcontrol) — Yeelight bulbs on the same REST contract, including on-device colour-flow
- [matter-homekit-bridge](https://github.com/dongnh/matter-homekit-bridge) and [matter-homekit-ac](https://github.com/dongnh/matter-homekit-ac) — publish Matter devices and AC tiles into Apple Home
- [matter-mac-presence](https://github.com/dongnh/matter-mac-presence), [matter-appletv-presence](https://github.com/dongnh/matter-appletv-presence), mac-status-bridge — Macs and Apple TV as occupancy sensors

See [CHANGELOG.md](CHANGELOG.md) for release notes and [CLAUDE.md](CLAUDE.md) for architecture rules.

## License

MIT.
