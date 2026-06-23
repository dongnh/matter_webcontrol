# Matter Web Controller

A Python server that exposes Matter smart-home devices and remote logical bridges as a unified REST + MCP API. Built on top of [`python-matter-server`](https://github.com/home-assistant-libs/python-matter-server).

- **REST** — control lights, read sensors, subscribe to occupancy events
- **MCP** — same operations available as tools for LLM integration
- **Federation** — chain multiple instances together over HTTP, no embedded code

---

## Quick start

```bash
pip install matter-web-controller

# Generate an API key (used by all clients via X-API-Key header)
export MATTER_SRV_KEY=$(openssl rand -hex 32)

# Start the server
matter-srv --fabric "My Home"

# In another terminal — talk to it
curl -H "X-API-Key: $MATTER_SRV_KEY" http://127.0.0.1:8080/api/status
# → {"lights_on":0,"lights_off":0,"sensors_active":0,"logical_bridges":0,"total_devices":0}

# Pair a Matter device (get the code from the device's app, e.g. Aqara Home)
curl -H "X-API-Key: $MATTER_SRV_KEY" -H "Content-Type: application/json" \
  -d '{"code":"2456-515-1552","name":"Living Room"}' \
  http://127.0.0.1:8080/api/register
```

Requires Python 3.12+.

---

## How it works

```
┌──────────────────────┐                ┌─────────────────────┐
│  REST clients        │                │  Other matter-srv   │
│  MCP / curl / app    │                │  (federation)       │
└──────────┬───────────┘                └──────────┬──────────┘
           │ X-API-Key                             │ X-API-Key
           ▼                                       ▼
        ┌────────────────────────────────────────────────┐
        │  matter-srv (FastAPI)                          │
        │  ┌──────────────┐    ┌──────────────────────┐  │
        │  │ MatterBridge │    │ LogicalBridgeManager │  │
        │  │  (WebSocket) │    │       (HTTP)         │  │
        │  └──────┬───────┘    └──────────┬───────────┘  │
        └─────────┼───────────────────────┼──────────────┘
                  ▼                       ▼
          python-matter-server      remote matter-srv
                  ▼                       ▼
             Matter LAN              another fabric
```

- The Matter bridge runs `python-matter-server` as a subprocess and connects via WebSocket. Device states are cached and event-driven, so REST responses don't block on protocol round-trips.
- Logical bridges federate with other matter-srv instances over plain REST. When a command targets a logical device, it's forwarded to the owning instance.
- Authentication is a single `X-API-Key` header. The same key is used for federation between instances.

---

## Privileges

`sudo` is **not** required when devices are already on the LAN. Matter operational traffic uses ordinary IPv6 UDP — no raw sockets, no Bluetooth.

| Scenario | Privilege needed |
|---|---|
| macOS, devices on LAN | None — run as your user |
| Linux, devices on LAN | None, or `setcap 'cap_net_raw,cap_net_admin+eip' $(readlink -f $(which python3))` if mDNS conflicts |
| BLE commissioning of new devices on Linux | `bluetooth` group + capabilities (or sudo) |
| BLE commissioning on macOS | None — system prompts for Bluetooth permission |

---

## CLI options

### `matter-srv`

| Flag | Default | Description |
|---|---|---|
| `--port` | `8080` | REST port. Matter WebSocket binds to `port + 1` |
| `--host` | `127.0.0.1` | Bind address. Use `0.0.0.0` to expose on LAN |
| `--api-key` | `$MATTER_SRV_KEY` | Required `X-API-Key` value. A **non-loopback bind without a key is refused** unless `--insecure` |
| `--fabric` | _(none)_ | Matter fabric label shown to commissioned devices |
| `--data-dir` | _(cwd)_ | Directory for caches + Matter fabric storage (or `$MATTER_DATA_DIR`). The absolute path is logged at startup |
| `--insecure` | _(off)_ | Allow a non-loopback bind without an API key |
| `--log-level` | `INFO` | Logging level (or `$MATTER_LOG_LEVEL`) |

### `matter-mcp`

Connects to a running `matter-srv` and exposes its operations as MCP tools.

| Flag | Default | Description |
|---|---|---|
| `--host` | `localhost` | matter-srv host |
| `--port` | `8080` | matter-srv port |
| `--api-key` | `$MATTER_SRV_KEY` | Forwarded as `X-API-Key` |
| `--transport` | `stdio` | `stdio` for local AI agents; `sse` / `http` for LAN access |
| `--mcp-host` | `127.0.0.1` | Bind host for `sse` / `http` transport (`0.0.0.0` for LAN) |
| `--mcp-port` | `7861` | Bind port for `sse` / `http` transport |
| `--log-level` | `INFO` | Logging level (or `$MATTER_LOG_LEVEL`) |

For `sse` / `http`, DNS-rebinding protection stays on: only the bind host and loopback are accepted as `Host` headers (a `0.0.0.0` bind also allows the machine hostname).

---

## REST API

All endpoints require `X-API-Key: $MATTER_SRV_KEY` (when `--api-key` is set).

Devices are addressed by stable hash-based IDs like `dev_a3f7c1b2`, derived from the hardware UniqueID. Aliases set via `/api/name` are display-only — they are **not** accepted as IDs anywhere.

Error mapping: `404` (device/alias unknown), `400` (bad parameters), `401` (auth), `405` (a mutation called with GET — they are POST-only), `503` (Matter bridge offline), `500` (other).

**Mutations are POST-only** and carry their parameters (including secrets like the peer api-key and pairing code) in the JSON body, never the URL.

### Read

| Method & Path | Description |
|---|---|
| `GET /api/status` | Counts: lights on/off, active sensors, ACs on/off, bridges, total devices |
| `GET /api/devices` | Raw list — every physical and logical device with `states` dict |
| `GET /api/lights` | Lights with normalized brightness (0.0–1.0) and temperature in Kelvin. `brightness` reflects the stored level even when off; `state` carries on/off |
| `GET /api/sensors` | All sensors with their metrics |
| `GET /api/sensor?id=...` | One sensor by ID |
| `GET /api/climate` | Temperature (°C) and humidity (%) for every reporting device — thermostat `local_temperature` + standalone temp/humidity sensors. Add `?id=…` for one device. |
| `GET /api/level?id=...` | Read raw brightness (0–254). POST `{"id","level"}` to set |
| `GET /api/mired?id=...` | Read color temperature (mireds). POST `{"id","mireds"}` to set |
| `GET /api/metadata` | Declarative bridge info (capabilities + states), used by federation peers |
| `GET /health` | Unauthenticated liveness — `{"status","bridge_ready"}` |
| `GET /version` | Unauthenticated version string |

### Control

| Method & Path | Body / Params |
|---|---|
| `POST /api/set` | `{"id":"dev_…","brightness":0.0–1.0,"temperature":Kelvin}` — both fields optional |
| `POST /api/toggle` | `{"id":"dev_…"}` — flip on/off (lights and ACs — for ACs, off→on resumes the last non-zero `system_mode`) |
| `POST /api/level` | `{"id":"dev_…","level":0–254}` |
| `POST /api/mired` | `{"id":"dev_…","mireds":153–500}` (clamped to Matter spec) |
| `POST /api/batch` | `{"actions":[{"id":..., "brightness":..., "temperature":...}, …]}` — runs in parallel |

```bash
# Set 80 % warm white on a device
curl -H "X-API-Key: $MATTER_SRV_KEY" \
  -X POST -H "Content-Type: application/json" \
  -d '{"id":"dev_e3798593","brightness":0.8,"temperature":2700}' \
  http://127.0.0.1:8080/api/set
```

### Management

| Method & Path | Body |
|---|---|
| `POST /api/name` | `{"id":"dev_…","name":"…"}` — assign alias |
| `POST /api/name/remove` | `{"id":"dev_…","name":"…"}` — remove alias |
| `POST /api/register` | `{"code":"…","name":"…","ip":"…"}` — commission a Matter device by pairing code (code in body) |
| `POST /api/unregister` | `{"node_id":N}` — unpair a fabric node (cleanup phantom entries) |
| `POST /api/refresh` | Re-pull caches from Matter server and logical bridges |

### Sensors (verified hardware)

All of the following Aqara sensors are bridged via **Hub M200** and surface through `/api/sensors` (and the SSE occupancy stream below). Each appears as one or more Matter endpoints with the listed clusters:

| Device | Connectivity | Matter clusters surfaced | Notes |
|---|---|---|---|
| **Motion Sensor P1** | Zigbee → M200 bridge | `OccupancySensing` (0x0406), `IlluminanceMeasurement` (0x0400), battery | PIR, battery-powered. Reports motion (occupancy 0/1) with a configurable hold time set in the Aqara app, plus ambient lux. |
| **Presence Sensor FP2** | Zigbee → M200 bridge | `OccupancySensing` per zone endpoint, `IlluminanceMeasurement` | mmWave, mains-powered, multi-zone. The hub bridges each configured zone as its own endpoint → its own `dev_*` ID, so up to ~30 independent occupancy IDs can come from a single FP2. |
| **Presence Sensor FP1E** | Zigbee → M200 bridge | `OccupancySensing`, presence/absence | mmWave single-zone, mains-powered. Faster and more reliable for "someone is sitting still" than the PIR P1. No illuminance. |
| **Presence Multi-Sensor FP300** | Zigbee → M200 bridge | `OccupancySensing`, `IlluminanceMeasurement`, `TemperatureMeasurement` (0x0402), `RelativeHumidityMeasurement` (0x0405) | mmWave + light + temp/humidity in one mains-powered unit. Temperature and humidity also show up in `/api/climate`. |

Notes:

- All four expose **`occupancy`** (0/1) on `/api/sensors` and emit on the `/api/subscribe` SSE feed (see below). Use the device's `dev_*` ID to subscribe.
- Hold/clear times, mmWave sensitivity, and FP2 zone layouts are configured in the **Aqara Home app**, not via Matter — those settings are not exposed as Matter attributes and so cannot be changed from this server.
- The FP2's per-zone endpoints each get their own stable `dev_*` ID; assign aliases via `POST /api/name` so the zones are recognizable.

### Air conditioners (Thermostat)

This setup is verified with **Aqara Hub M200** + **Aqara Climate Sensor W100**, following Aqara's documented "Climate Sensor as Thermostat" pattern: the W100 carries the IR blaster and on-board temperature/humidity sensor and is paired (in the Aqara Home app) to the target AC. The M200 then bridges the W100 over Matter as a single **Thermostat** endpoint (cluster `0x0201` / 513) — `local_temperature` is the W100's measured room temperature, and writes to `system_mode` / `*_setpoint` are translated by the W100 into IR commands to the AC. There is no Scenes-cluster bridging, so on/off and setpoint must be written directly via Thermostat attributes. The same path applies to other Matter thermostats.

State surfaced (alongside the standard `/api/devices` entry):

| Field | Meaning |
|---|---|
| `system_mode` | `0`=Off, `1`=Auto, `3`=Cool, `4`=Heat, `5`=EmergencyHeat, `6`=Precooling, `7`=FanOnly, `8`=Dry, `9`=Sleep |
| `on` | `system_mode != 0` |
| `local_temperature` | °C, read-only |
| `cooling_setpoint` / `heating_setpoint` | °C |
| `fan_speed` | `0–100`, only for logical-bridge ACs that report it |

| Method & Path | Body |
|---|---|
| `GET /api/acs` | List all AC/Thermostat devices |
| `GET /api/ac?id=…` | Read one AC (state, setpoints) |
| `POST /api/ac` | `{"id":"dev_…","on":true,"mode":3,"setpoint":26.0,"fan_speed":50}` |

Notes:
- `on=true` resumes the last non-zero `system_mode` (default Cool); `on=false` writes `system_mode=0`.
- `mode` overrides `on`; `system_mode` is accepted as a synonym for `mode`.
- `setpoint` is in °C (1/100 °C internally) and is routed to the **heating** setpoint in Heat/EmergencyHeat or the **cooling** setpoint in Cool/Precooling, picked by the effective mode. A single `setpoint` in Auto is rejected (the deadband needs both).
- `fan_speed` (0–100) is forwarded to logical-bridge ACs that support it; it is **rejected** on physical Matter ACs (no Thermostat fan attribute).
- `POST /api/toggle` and `POST /api/set` (brightness 0/1) also work on AC IDs — they map to on/off only.

```bash
# Turn on the Office AC, Cool mode, 26°C
curl -H "X-API-Key: $MATTER_SRV_KEY" -H "Content-Type: application/json" \
  -d '{"id":"dev_78c2bf4d","on":true,"mode":3,"setpoint":26.0}' \
  http://127.0.0.1:8080/api/ac
```

> **Aqara hub bridging caveat (verified on Hub M200 + Climate Sensor W100):** Aqara does **not** bridge its in-app Scenes through Matter (no `Scenes` / `ScenesManagement` cluster on either the M200 hub or the W100-as-Thermostat endpoint it bridges). Hub-side scenes like `Office_AC26Auto` are only reachable via the Aqara app or their own automations. Use the Thermostat write path above to drive the W100 directly from Matter — the W100 then emits the IR commands to the AC.

### Federation

| Method & Path | Body |
|---|---|
| `POST /api/bridge` | `{"ip":"…","port":N,"api_key":"…"}` — register a remote matter-srv (key in body, not URL) |
| `POST /api/bridge/remove` | `{"ip":"…","port":N}` — unregister |

When a peer is registered, all of its devices appear in `/api/devices` of this instance, and control commands are forwarded automatically. Registration validates the target (private/loopback IPs or LAN hostnames only) and rejects registering the instance against itself.

### SSE — occupancy stream

```bash
curl -N -H "X-API-Key: $MATTER_SRV_KEY" \
  "http://127.0.0.1:8080/api/subscribe?id=dev_b503384e"
```

```
data: {"id":"dev_b503384e","occupancy":1,"timestamp":"2025-05-03T17:30:00+00:00"}

: keepalive

data: {"id":"dev_b503384e","occupancy":0,"timestamp":"2025-05-03T17:31:42+00:00"}
```

A `: keepalive` comment is sent every 15 s so client disconnects are detected even when no events fire.

---

## MCP integration

Start `matter-srv` first, then point an MCP client at `matter-mcp`. The MCP server is a thin HTTP client — every tool call hits the REST API, so the same auth rules apply.

**Claude Desktop:**

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

**Available tools:** `get_devices`, `get_lights`, `get_sensors`, `get_sensor`, `get_climate`, `get_status`, `set_device`, `toggle`, `set_level`, `set_mired`, `batch_control`, `set_name`, `remove_name`, `add_bridge`, `remove_bridge`, `register_device`, `unregister_node`, `list_acs`, `get_ac`, `set_ac`, `get_metadata`, `refresh`. (Most map to a REST endpoint; the SSE `/api/subscribe` stream is HTTP-only and has no MCP tool.)

---

## Federation example

Two instances on the same LAN, each with their own devices:

```bash
# On host A (10.0.0.10)
export MATTER_SRV_KEY=keyA
matter-srv --host 0.0.0.0 --fabric "Floor 1"

# On host B (10.0.0.11)
export MATTER_SRV_KEY=keyB
matter-srv --host 0.0.0.0 --fabric "Floor 2"

# From host A — register B (peer key in the body, never the URL)
curl -H "X-API-Key: keyA" -H "Content-Type: application/json" \
  -d '{"ip":"10.0.0.11","port":8080,"api_key":"keyB"}' \
  http://127.0.0.1:8080/api/bridge
```

After registration, B's devices appear in `curl http://A:8080/api/devices`, and any `/api/set` against a B-owned ID is transparently forwarded over HTTP. No code execution, no script blobs — just REST.

---

## Limitations

- Only Matter devices already on the LAN are supported. Non-Matter hardware (Casambi, Yeelight, etc.) requires a third-party adapter that exposes the matter-srv REST API.
- BLE commissioning is not enabled by default in `python-matter-server` builds — `commission_with_code` uses `network_only=True`. Devices must be discoverable via mDNS.
- One fabric per instance.

---

## Development

```bash
git clone https://github.com/dongnh/matter_webcontrol.git
cd matter_webcontrol
python3.12 -m venv venv
venv/bin/pip install -e .

# Run the smoke tests (uses fake bridges, no hardware needed)
./dev/start_two.sh
A_KEY=keyA B_KEY=keyB ./dev/smoke.sh
./dev/stop.sh
```

The `dev/` harness boots two `matter-srv` instances with pre-baked fake devices and runs ~20 curl assertions covering auth, control, federation, batch, and metadata.

See [CLAUDE.md](CLAUDE.md) for the architecture rules followed when adding endpoints.
