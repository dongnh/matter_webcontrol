# Matter Web Controller

This document explains how the local Matter device management system works using a web interface.

## System Architecture

The system separates background tasks and simplifies network rules to make it easier to use:

1. **Background Process:** The app runs the standard `python-matter-server` as a separate background task. This keeps the heavy work away from the web server.
2. **Event-Driven Caching & Offline-First:** The web server subscribes to device events and maintains local JSON/text caches (`devices_cache.txt`, `occupancy_cache.json`, and `names_cache.json`). The caches are hydrated immediately upon server startup to ensure seamless continuity and non-blocking, immediate API responses.
3. **HTTP Setup:** The web server acts as a middleman. It changes complex WebSocket data into simple HTTP requests (GET/POST). Users only need to communicate via standard web addresses to interact with JSON data.

## Requirements

Python 3.12 or newer.

## Installation

Create a virtual environment and install the package using your package manager. This will automatically install required tools like `aiohttp` and `home-assistant-chip-core`.

## How to Run

Start the system by typing the executable command `matter-srv`. You can use the `--port` parameter to set the web server port. The default is 8080. The background Matter server will automatically use the next port number.

## API Endpoints

**Note on Device IDs and Aliases:** All devices use a standardized composite ID format: `dev_{node_id}_{endpoint_id}`. You can assign unique human-readable names (aliases) to devices using the `/api/name` endpoint. These names can be used interchangeably with the standard ID in all control and query APIs.

### Get all cached devices

* **URL:** `/api/devices`
* **Method:** `GET`
* **Description:** Retrieves the complete list of all devices, their assigned names, and raw states directly from the local cache. 
* **Example:** `http://localhost:8080/api/devices`

### Get lighting device status

* **URL:** `/api/lights`
* **Method:** `GET`
* **Description:** Retrieves cached lighting states. The data includes the standardized ID, an array of assigned names, power state, normalized brightness level (0.0 to 1.0), and color temperature (in Kelvin).
* **Example:** `http://localhost:8080/api/lights`

### Get sensor device status

* **URL:** `/api/sensors`
* **Method:** `GET`
* **Description:** Retrieves aggregated sensor states from the cache. Includes standardized ID, an array of assigned names, and a human-readable timestamp (`occupancy_last_active`) for occupancy sensors.
* **Example:** `http://localhost:8080/api/sensors`

### Get specific sensor status

* **URL:** `/api/sensor`
* **Method:** `GET`
* **Description:** Retrieves the state and formatted occupancy history for a specific sensor.
* **Parameters:**
  * `id` (string, required): The standardized ID or assigned name of the device.
* **Example:** `http://localhost:8080/api/sensor?id=LivingRoomSensor`

### Assign a name to a device

* **URL:** `/api/name`
* **Method:** `GET` or `POST`
* **Description:** Assigns a unique alias to a device. The system enforces global uniqueness for each name across all devices.
* **Parameters / JSON Body:**
  * `id` (string, required): The standard ID or existing name of the device.
  * `name` (string, required): The new unique name to assign.
* **Example (POST):** `curl -X POST -H "Content-Type: application/json" -d '{"id": "dev_1_8", "name": "Living Room Light"}' http://localhost:8080/api/name`

### Commission a new Matter device

* **URL:** `/api/register`
* **Method:** `GET`
* **Description:** Initiates the commissioning process for a new device on the local network. 
* **Parameters:**
  * `code` (string, required): The Matter setup payload code.
  * `ip` (string, optional): The IP address of the device for direct network commissioning.
  * `name` (string, optional): A pending name to assign to the device upon successful commissioning.
* **Example:** `http://localhost:8080/api/register?code=11223344556&name=KitchenLight`

### Control a lighting device

* **URL:** `/api/set`
* **Method:** `GET` or `POST`
* **Description:** Controls the brightness and color temperature of a specific lighting device. 
* **Parameters / JSON Body:**
  * `id` (string, required): The standardized ID or assigned name (e.g., `dev_1_8` or `Living Room Light`).
  * `brightness` (float, optional): The desired brightness level from 0.0 to 1.0. Setting to 0.0 powers off the device.
  * `temperature` (integer, optional): The desired color temperature in Kelvin.
* **Example (POST):** `curl -X POST -H "Content-Type: application/json" -d '{"id": "Living Room Light", "brightness": 0.8, "temperature": 4000}' http://localhost:8080/api/set`