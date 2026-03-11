# Matter Web Controller

This document delineates the operational framework of the hybrid device management system, unifying physical Matter networks and virtual logical bridges via a standardized web interface.

## System Architecture

The architecture implements a dual-stack aggregation model to optimize performance and interoperability:

1. **Physical Matter Subsystem:** The application executes `python-matter-server` as an isolated background daemon. This isolates heavy protocol operations from the primary web server.

2. **Logical Bridge Integration:** The system incorporates third-party control planes (e.g., Casambi) via HTTP metadata polling. It automatically normalizes scale mismatches (e.g., mapping raw Casambi values to standardized levels).

3. **Event-Driven Caching:** The web server subscribes to hardware events and maintains local persistence arrays (`devices_cache.txt`, `bridge_cache.json`, etc.). Caches are hydrated immediately upon initialization to guarantee non-blocking, asynchronous API responses.

4. **Unified Abstraction Layer:** Complex websocket operations are abstracted into standard HTTP requests and Server-Sent Events (SSE) streams. Control commands are dynamically routed to physical or logical nodes based on identifier resolution.

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
