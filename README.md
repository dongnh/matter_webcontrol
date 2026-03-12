# Matter Web Controller

This document delineates the operational framework of the hybrid device management system, unifying physical Matter networks and virtual logical bridges via a standardized web interface.

## System Architecture
The architecture implements a dual-stack aggregation model to optimize performance and interoperability:

1. Physical Matter Subsystem: The application executes `python-matter-server` as an isolated background daemon. This isolates heavy protocol operations from the primary web server.
2. Logical Bridge Integration: The system incorporates third-party control planes (e.g., Casambi) via HTTP metadata polling. It automatically normalizes scale mismatches (e.g., mapping raw Casambi values to standardized levels).
3. Event-Driven Caching: The web server subscribes to hardware events and maintains local persistence arrays (`devices_cache.txt`, `bridge_cache.json`, etc.). Caches are hydrated immediately upon initialization to guarantee non-blocking, asynchronous API responses.
4. Unified Abstraction Layer: Complex websocket operations are abstracted into standard HTTP requests and Server-Sent Events (SSE) streams. Control commands are dynamically routed to physical or logical nodes based on identifier resolution.
, etc.). Caches are hydrated immediately upon initialization to guarantee non-blocking, asynchronous API responses.

## Limitations
* Network Provisioning: The system exclusively supports Matter devices previously connected to the Local Area Network (LAN). Consult individual device documentation for network inclusion procedures.
* Hardware Compatibility: Native support is restricted to bridges, lighting devices, motion sensors, and presence sensors.
* External Ecosystems: Non-Matter hardware architectures, such as Casambi or Yeelight, require integration via the Logical Bridge mechanism.

## Requirements
Python 3.12 or newer.

## Installation
Create a virtual environment and execute the package installation. Dependencies such as `aiohttp`, `fastapi`, and `home-assistant-chip-core` are resolved automatically.

## How to Run
Execute the command `matter-srv`. Utilize the `--port` argument to specify the web server port (default: 8080). The background Matter process inherently binds to the subsequent port integer.

## API Endpoints

**Note on Device Identifiers:** All hardware and virtual nodes utilize a standardized hash identifier format (`dev_{node_id}_{endpoint_id}`). Aliases assigned via `/api/name` function interchangeably with standard IDs across all control protocols.

### Add a Logical Bridge
* URL `/api/bridge`
* Method `GET`
* Description: Registers a new logical bridge and persists its network configuration to the local cache.
* Parameters:
  * `ip` (string, required): The IPv4 address of the logical bridge.
  * `port` (integer, required): The communication port.
* Example: `http://localhost:8080/api/bridge?ip=192.168.1.220&port=8000`

### Get all cached devices
* URL: `/api/devices`
* Method: `GET`
* Description: Retrieves the unified list of all local Matter devices and registered logical devices, including their aliases and raw states.
* Example: `http://localhost:8080/api/devices`

### Get lighting device status
* URL: `/api/lights`
* Method: `GET`
* Description: Aggregates lighting states across physical and logical nodes. Data includes the standardized ID, aliases, Boolean power state, normalized brightness (0.0 to 1.0), and color temperature in Kelvin (if applicable).
* Example: `http://localhost:8080/api/lights`

### Get sensor device status
* URL: `/api/sensors`
* Method: `GET`
* Description: Retrieves sensor metrics from physical Matter nodes. Includes standard identifiers, aliases, normalized sensor values, and occupancy timestamps formatted in ISO 8601 UTC.
* Example: `http://localhost:8080/api/sensors`

### Assign a name to a device
* URL: `/api/name`
* Method: `GET` or `POST`
* Description: Assigns a globally unique alias to a physical or logical device identifier.
* Parameters / JSON Body:
  * `id` (string, required): The standard ID or existing alias.
  * `name` (string, required): The target unique string to assign.
* Example (POST): `curl -X POST -H "Content-Type: application/json" -d '{"id": "dev_1_8", "name": "Main Hall"}' http://localhost:8080/api/name`

### Commission a new Matter device
* URL: `/api/register`
* Method: `GET`
* Description: Executes network inclusion routines for unprovisioned physical Matter hardware.
* Parameters:
  * `code` (string, required): The standard manual pairing payload.
  * `ip` (string, optional): Target IP address for localized IP-based commissioning.
  * `name` (string, optional): A pending alias mapped post-commissioning.
* Example: `http://localhost:8080/api/register?code=11223344556&name=Kitchen`

### Control a lighting device

* URL: `/api/set`
* Method: `GET` or `POST`
* Description: Actuates state mutation. The server dynamically routes the payload to Matter clusters for physical devices or executes logical protocols for virtual nodes.
* Parameters / JSON Body:
  * `id` (string, required): The standardized ID or alias.
  * `brightness` (float, optional): Target level (0.0 to 1.0).
  * `temperature` (integer, optional): Target color temperature (Kelvin).

* Example (POST): `curl -X POST -H "Content-Type: application/json" -d '{"id": "Sofa and Painting", "brightness": 0.8}' http://localhost:8080/api/set`

### Subscribe to occupancy events
* URL: `/api/subscribe`
* Method: `GET`
* Description: Establishes a Server-Sent Events (SSE) stream to transmit real-time occupancy state mutations. The payload includes the target identifier, integer state, and an ISO 8601 timestamp.
* Parameters:
  * `id` (string, required): The standard ID or alias of the target sensor.
* Example: `curl -N -H "Accept: text/event-stream" "http://localhost:8080/api/subscribe?id=Motion_Entry"`

### Get logical bridge metadata
* URL: `/api/metadata`
* Method: `GET`
* Description: Outputs a JSON payload describing the server network as a logical lighting controller. It dynamically detects all physical Matter devices and external logical nodes, generating executable Python scripts mapped to standard Matter events (e.g., turn_on, set_level, subscribe_occupancy).
* Example: `http://localhost:8080/api/metadata`
* Sample Response:
  ```json
      {
        "bridge": 
        {
          "id": "matter_bridge_http",
          "type": "lighting_controller",
          "network_host": "192.168.1.220",
          "network_port": 8080
        },      
        "devices": 
        [
          {
            "node_id": "matter_light_1",
            "name": "Tunable Desk Light",
            "hardware_type": "color_temperature_light",
            "events": 
            {
              "turn_on": 
              {
                "trigger": "on_off_cluster",
                "script": "import urllib.request\n# Execute GET request to turn on\nurllib.request.urlopen('http://192.168.1.220:8080/api/set?id=matter_light_1&brightness=1.0')"
              },
              "turn_off": 
              {
                "trigger": "on_off_cluster",
                "script": "import urllib.request\n# Execute GET request to turn off\nurllib.request.urlopen('http://192.168.1.220:8080/api/set?id=matter_light_1&brightness=0.0')"
              },
              "set_level": 
              {
                "trigger": "level_control_cluster",
                "script": "import sys, urllib.request\n# Parse Matter level (0-254) and convert to float brightness (0.0-1.0)\nmatter_level = int(sys.argv[1]) if len(sys.argv) > 1 else 254\nbrightness = matter_level / 254.0\nurllib.request.urlopen(f'http://192.168.1.220:8080/api/set?id=matter_light_1&brightness={brightness}')"
              },
              "read_level": 
              {
                "trigger": "level_control_cluster",
                "script": "import urllib.request, json\n# Retrieve float brightness from API and convert to Matter level (0-254)\nresponse = urllib.request.urlopen('http://192.168.1.220:8080/api/lights')\ndata = json.loads(response.read().decode('utf-8'))\ndevice = next((d for d in data if d.get('id') == 'matter_light_1'), {})\nbrightness = device.get('brightness', 0.0)\nprint(int(brightness * 254))"
              },
              "set_color_temperature": 
              {
                "trigger": "color_control_cluster",
                "script": "import sys, urllib.request\n# Parse Mireds and convert to Kelvin\nmireds = int(sys.argv[1]) if len(sys.argv) > 1 else 250\nkelvin = int(1000000 / mireds) if mireds > 0 else 4000\nurllib.request.urlopen(f'http://192.168.1.220:8080/api/set?id=matter_light_1&temperature={kelvin}')"
              },
              "read_color_temperature": 
              {
                "trigger": "color_control_cluster",
                "script": "import urllib.request, json\n# Retrieve Kelvin from API and convert to Mireds\nresponse = urllib.request.urlopen('http://192.168.1.220:8080/api/lights')\ndata = json.loads(response.read().decode('utf-8'))\ndevice = next((d for d in data if d.get('id') == 'matter_light_1'), {})\nkelvin = device.get('temperature', 4000)\nmireds = int(1000000 / kelvin) if kelvin and kelvin > 0 else 0\nprint(mireds)"
              }
            }
          },
          {
            "node_id": "matter_sensor_1",
            "name": "Motion Sensor",
            "hardware_type": "occupancy_sensor",
            "events": 
            {
              "read_occupancy": 
              {
                "trigger": "occupancy_sensing_cluster",
                "script": "import urllib.request, json\n# Poll current occupancy status\nresponse = urllib.request.urlopen('http://192.168.1.220:8080/api/sensor?id=matter_sensor_1')\ndata = json.loads(response.read().decode('utf-8'))\nprint(data.get('occupancy', 0))"
              },
              "subscribe_occupancy": 
              {
                "trigger": "occupancy_sse_stream",
                "script": "import urllib.request\n# Connect to Server-Sent Events stream\nresponse = urllib.request.urlopen('http://192.168.1.220:8080/api/subscribe?id=matter_sensor_1')\nfor line in response:\n    print(line.decode('utf-8').strip())"
              }
            }
          }
        ]
      }
    ```  