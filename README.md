# Matter Web Controller

A Python server that bridges Matter smart home devices and external logical bridges (e.g., Casambi) into a unified REST + MCP interface.

## Architecture

1. **Matter Bridge** — Runs `python-matter-server` as a background process and connects via WebSocket. Device states are cached locally for fast, non-blocking API responses.
2. **Logical Bridges** — Integrates third-party systems over HTTP. Brightness scale mismatches (0–1 vs 0–254) are auto-normalized.
3. **Event-Driven Cache** — Subscribes to Matter attribute changes and persists state to `devices_cache.txt`, `bridge_cache.json`, etc. Caches are loaded on startup.
4. **Unified API** — All WebSocket/protocol complexity is abstracted into simple HTTP endpoints and SSE streams. Commands are routed to physical or logical devices based on ID resolution.

## Limitations

- Only Matter devices already on the LAN are supported. See device docs for network provisioning.
- Native support: bridges, lights, motion sensors, presence sensors.
- Non-Matter hardware (Casambi, Yeelight, etc.) requires integration via Logical Bridge.

## Requirements

Python 3.12+

## Installation

Create a virtual environment and install the package. Dependencies (`aiohttp`, `fastapi`, `home-assistant-chip-core`, etc.) are resolved automatically.

## Usage

### HTTP Server

```bash
sudo matter-srv                          # starts on port 8080
sudo matter-srv --port 9090              # custom port
sudo matter-srv --fabric "Home Lab"      # set Matter fabric label
```

### MCP Server

The MCP server connects to a running `matter-srv` instance via HTTP.

```bash
matter-mcp                               # connects to localhost:8080
matter-mcp --host 192.168.1.10           # remote server
matter-mcp --port 9090                   # custom port
```

> **Note:** `sudo` is recommended because Matter uses BLE and low-level network interfaces that require root privileges for device commissioning and discovery.

**HTTP server options:**

| Option     | Default | Description                    |
|------------|---------|--------------------------------|
| `--port`   | 8080    | Web server port                |
| `--fabric` | _(none)_| Matter fabric label            |

**MCP server options:**

| Option     | Default     | Description                    |
|------------|-------------|--------------------------------|
| `--host`   | `localhost` | HTTP server host               |
| `--port`   | 8080        | HTTP server port               |

The Matter server process binds to `port + 1` automatically.

## API Reference

> **Device IDs:** Devices use stable hash-based IDs derived from hardware UniqueID (e.g. `dev_a3f7c1b2`). All endpoints require the canonical `dev_*` ID. Aliases set via `/api/name` are for display purposes only.

### `GET /api/bridge` — Register a logical bridge

| Param | Type   | Required | Description              |
|-------|--------|----------|--------------------------|
| `ip`  | string | yes      | Bridge IPv4 address      |
| `port`| int    | yes      | Bridge port              |

```
GET /api/bridge?ip=192.168.1.220&port=8000
```

### `GET /api/devices` — List all devices

Returns all physical and logical devices with their raw states and aliases.

```
GET /api/devices
```

### `GET /api/lights` — List lighting devices

Returns lights with normalized brightness (0.0–1.0) and color temperature (Kelvin).

```
GET /api/lights
```

### `GET /api/sensors` — List sensor devices

Returns sensors with their metrics and occupancy timestamps.

```
GET /api/sensors
```

### `GET /api/sensor` — Get a single sensor

| Param | Type   | Required | Description        |
|-------|--------|----------|--------------------|
| `id`  | string | yes      | Device ID          |

```
GET /api/sensor?id=Motion_Entry
```

### `GET|POST /api/name` — Assign a device alias

| Param  | Type   | Required | Description        |
|--------|--------|----------|--------------------|
| `id`   | string | yes      | Device ID          |
| `name` | string | yes      | New unique alias   |

```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"id": "dev_1_8", "name": "Main Hall"}' \
  http://localhost:8080/api/name
```

### `GET /api/name/remove` — Remove a device alias

| Param  | Type   | Required | Description        |
|--------|--------|----------|--------------------|
| `id`   | string | yes      | Device ID          |
| `name` | string | yes      | Alias to remove    |

```
GET /api/name/remove?id=dev_a3f7c1b2&name=Main Hall
```

### `GET /api/status` — Quick device summary

Returns counts: lights on/off, active sensors, connected bridges, total devices.

```
GET /api/status
```

### `GET /api/toggle` — Toggle a device on/off

| Param | Type   | Required | Description        |
|-------|--------|----------|--------------------|
| `id`  | string | yes      | Device ID          |

```
GET /api/toggle?id=Main Hall
```

### `GET /api/bridge/remove` — Remove a logical bridge

| Param | Type   | Required | Description         |
|-------|--------|----------|---------------------|
| `ip`  | string | yes      | Bridge IPv4 address |
| `port`| int    | yes      | Bridge port         |

```
GET /api/bridge/remove?ip=192.168.1.220&port=8000
```

### `POST /api/batch` — Control multiple devices

```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"actions": [{"id": "dev_a1b2", "brightness": 0.5}, {"id": "dev_c3d4", "brightness": 0}]}' \
  http://localhost:8080/api/batch
```

### `GET /api/register` — Commission a Matter device

| Param  | Type   | Required | Description                |
|--------|--------|----------|----------------------------|
| `code` | string | yes      | Manual pairing code        |
| `ip`   | string | no       | Target IP for commissioning|
| `name` | string | no       | Alias to assign after join |

```
GET /api/register?code=11223344556&name=Kitchen
```

### `GET|POST /api/set` — Control a device

| Param         | Type   | Required | Description                   |
|---------------|--------|----------|-------------------------------|
| `id`          | string | yes      | Device ID or alias            |
| `brightness`  | float  | no       | Target brightness (0.0–1.0)   |
| `temperature` | int    | no       | Target color temperature (K)  |

```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"id": "Sofa and Painting", "brightness": 0.8}' \
  http://localhost:8080/api/set
```

### `GET|POST /api/level` — Get/set raw brightness (0–254)

| Param   | Type | Required | Description                       |
|---------|------|----------|-----------------------------------|
| `id`    | str  | yes      | Device ID or alias                |
| `level` | int  | no       | Raw level (0–254). Omit to read.  |

```
GET /api/level?id=dev_1_8
GET /api/level?id=dev_1_8&level=200
```

### `GET|POST /api/mired` — Get/set color temperature (mireds)

| Param   | Type | Required | Description                         |
|---------|------|----------|-------------------------------------|
| `id`    | str  | yes      | Device ID or alias                  |
| `mireds`| int  | no       | Color temp in mireds. Omit to read. |

```
GET /api/mired?id=dev_1_8
GET /api/mired?id=dev_1_8&mireds=250
```

### `GET /api/subscribe` — SSE stream for occupancy events

| Param | Type   | Required | Description        |
|-------|--------|----------|--------------------|
| `id`  | string | yes      | Device ID          |

```bash
curl -N -H "Accept: text/event-stream" \
  "http://localhost:8080/api/subscribe?id=Motion_Entry"
```

Each event: `{"id": "...", "occupancy": 0|1, "timestamp": "ISO 8601"}`

### `GET /api/refresh` — Force refresh all device states

Refreshes both the Matter cache and all logical bridge metadata.

```
GET /api/refresh
```

### `GET /api/metadata` — Bridge metadata for federation

Returns this server's device list with executable Python scripts for each event, allowing other instances to integrate as a logical bridge.

<details>
<summary>Example response</summary>

```json
{
  "bridge": {
    "id": "matter_bridge_http",
    "type": "lighting_controller",
    "network_host": "192.168.1.220",
    "network_port": 8080
  },
  "devices": [
    {
      "node_id": "dev_1_8",
      "name": "Tunable Desk Light",
      "hardware_type": "color_temperature_light",
      "events": {
        "turn_on": {
          "trigger": "on_off_cluster",
          "script": "import urllib.request\nurllib.request.urlopen('http://192.168.1.220:8080/api/set?id=dev_1_8&brightness=1.0')"
        },
        "turn_off": {
          "trigger": "on_off_cluster",
          "script": "import urllib.request\nurllib.request.urlopen('http://192.168.1.220:8080/api/set?id=dev_1_8&brightness=0.0')"
        },
        "set_level": {
          "trigger": "level_control_cluster",
          "script": "import sys, urllib.request\nlevel = int(sys.argv[1]) if len(sys.argv) > 1 else 254\nurllib.request.urlopen(f'http://192.168.1.220:8080/api/level?id=dev_1_8&level={level}')"
        },
        "read_level": {
          "trigger": "level_control_cluster",
          "script": "import urllib.request, json\nresponse = urllib.request.urlopen('http://192.168.1.220:8080/api/level?id=dev_1_8')\ndata = json.loads(response.read().decode('utf-8'))\nprint(data.get('level', 0))"
        },
        "set_color_temperature": {
          "trigger": "color_control_cluster",
          "script": "import sys, urllib.request\nmireds = int(sys.argv[1]) if len(sys.argv) > 1 else 250\nurllib.request.urlopen(f'http://192.168.1.220:8080/api/mired?id=dev_1_8&mireds={mireds}')"
        },
        "read_color_temperature": {
          "trigger": "color_control_cluster",
          "script": "import urllib.request, json\nresponse = urllib.request.urlopen('http://192.168.1.220:8080/api/mired?id=dev_1_8')\ndata = json.loads(response.read().decode('utf-8'))\nprint(data.get('mireds', 0))"
        }
      }
    },
    {
      "node_id": "dev_2_1",
      "name": "Motion Sensor",
      "hardware_type": "occupancy_sensor",
      "events": {
        "read_occupancy": {
          "trigger": "occupancy_sensing_cluster",
          "script": "import urllib.request, json\nresponse = urllib.request.urlopen('http://192.168.1.220:8080/api/sensor?id=dev_2_1')\ndata = json.loads(response.read().decode('utf-8'))\nprint(data.get('occupancy', 0))"
        },
        "subscribe_occupancy": {
          "trigger": "occupancy_sse_stream",
          "script": "import urllib.request\nresponse = urllib.request.urlopen('http://192.168.1.220:8080/api/subscribe?id=dev_2_1')\nfor line in response:\n    print(line.decode('utf-8').strip())"
        }
      }
    }
  ]
}
```

</details>

## MCP Integration

The `matter-mcp` command connects to a running `matter-srv` and exposes its operations as MCP tools, allowing LLMs to control Matter devices directly. Start the HTTP server first, then run the MCP server.

### Available Tools

| Tool | Description |
|------|-------------|
| `get_devices` | List all devices with states and aliases |
| `get_lights` | List lights with brightness and temperature |
| `get_sensors` | List sensors with metrics |
| `get_sensor` | Get a single sensor by ID/alias |
| `get_status` | Quick summary (lights on/off, sensors, bridges) |
| `set_device` | Control brightness and/or color temperature |
| `toggle` | Toggle device on/off |
| `set_level` | Set raw brightness (0–254) |
| `set_mired` | Set color temperature (mireds) |
| `batch_control` | Control multiple devices at once |
| `set_name` | Assign alias to device |
| `remove_name` | Remove alias from device |
| `add_bridge` | Register logical bridge |
| `remove_bridge` | Remove logical bridge |
| `register_device` | Commission new Matter device |
| `refresh` | Force refresh all states |

### Claude Desktop Configuration

```json
{
  "mcpServers": {
    "matter": {
      "command": "matter-mcp",
      "args": ["--host", "localhost", "--port", "8080"]
    }
  }
}
```
