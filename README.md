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
curl -H "X-API-Key: $MATTER_SRV_KEY" \
  "http://127.0.0.1:8080/api/register?code=2456-515-1552&name=Living%20Room"
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
| `--host` | `127.0.0.1` | Bind address. Use `0.0.0.0` to expose on LAN (api-key required) |
| `--api-key` | `$MATTER_SRV_KEY` | Required header value. If unset and `--host 0.0.0.0`, a warning is logged |
| `--fabric` | _(none)_ | Matter fabric label shown to commissioned devices |

### `matter-mcp`

Connects to a running `matter-srv` and exposes its operations as MCP tools.

| Flag | Default | Description |
|---|---|---|
| `--host` | `localhost` | matter-srv host |
| `--port` | `8080` | matter-srv port |
| `--api-key` | `$MATTER_SRV_KEY` | Forwarded as `X-API-Key` |

---

## REST API

All endpoints require `X-API-Key: $MATTER_SRV_KEY` (when `--api-key` is set).

Devices are addressed by stable hash-based IDs like `dev_a3f7c1b2`, derived from the hardware UniqueID. Aliases set via `/api/name` are display-only — they are **not** accepted as IDs anywhere.

Error mapping: `404` (device/alias unknown), `400` (bad parameters), `401` (auth), `503` (Matter bridge offline), `500` (other).

### Read

| Method & Path | Description |
|---|---|
| `GET /api/status` | Counts: lights on/off, active sensors, bridges, total devices |
| `GET /api/devices` | Raw list — every physical and logical device with `states` dict |
| `GET /api/lights` | Lights with normalized brightness (0.0–1.0) and temperature in Kelvin |
| `GET /api/sensors` | All sensors with their metrics |
| `GET /api/sensor?id=...` | One sensor by ID |
| `GET /api/level?id=...` | Read raw brightness (0–254). Add `&level=N` to set |
| `GET /api/mired?id=...` | Read color temperature (mireds). Add `&mireds=N` to set |
| `GET /api/metadata` | Declarative bridge info (capabilities + states), used by federation peers |

### Control

| Method & Path | Body / Params |
|---|---|
| `POST /api/set` | `{"id":"dev_…","brightness":0.0–1.0,"temperature":Kelvin}` — both fields optional |
| `GET /api/toggle?id=...` | Flip on/off |
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

| Method & Path | Params |
|---|---|
| `POST /api/name` | `{"id":"dev_…","name":"…"}` — assign alias |
| `GET /api/name/remove?id=&name=` | Remove alias |
| `GET /api/register?code=&name=&ip=` | Commission a Matter device by pairing code |
| `GET /api/refresh` | Re-pull caches from Matter server and logical bridges |

### Federation

| Method & Path | Params |
|---|---|
| `GET /api/bridge?ip=&port=&api_key=` | Register a remote matter-srv as a logical bridge |
| `GET /api/bridge/remove?ip=&port=` | Unregister |

When a peer is registered, all of its devices appear in `/api/devices` of this instance, and control commands are forwarded automatically.

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

**Available tools** (one per REST endpoint): `get_devices`, `get_lights`, `get_sensors`, `get_sensor`, `get_status`, `set_device`, `toggle`, `set_level`, `set_mired`, `batch_control`, `set_name`, `remove_name`, `add_bridge`, `remove_bridge`, `register_device`, `refresh`.

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

# From host A — register B
curl -H "X-API-Key: keyA" \
  "http://127.0.0.1:8080/api/bridge?ip=10.0.0.11&port=8080&api_key=keyB"
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
