# Matter Web Controller

This document explains how the local Matter device management system works using a web interface.

## System Architecture

The system separates background tasks and simplifies network rules to make it easier to use:

1. **Background Process:** The app runs the standard `python-matter-server` as a separate background task. This keeps the heavy work away from the web server.
2. **WebSockets Connection:** The web server keeps a constant connection to the background task. This updates the network data in real time without slowing things down.
3. **HTTP Setup:** The web server acts as a middleman. It changes complex WebSocket data into a simple HTTP request (GET/POST). Users only need to communicate via standard web addresses to interact with JSON data.

## Requirements

Python 3.12 or newer.

## Installation

Create a virtual environment and install the package using your package manager. This will automatically install required tools like `aiohttp` and `home-assistant-chip-core`.

## How to Run

Start the system by typing the executable command `matter-srv`. You can use the `--port` parameter to set the web server port. The default is 8080. The background Matter server will automatically use the next port number.

## API Endpoints

### Get lighting device status

* **URL:** `/api/lights`
* **Method:** `GET`
* **Description:** Gets a list of all devices that have lighting features on the local Matter network. The data includes the Node ID, Endpoint ID, power state, normalized brightness level (0.0 to 1.0), and color temperature (in Kelvin).
* **Example:** `http://localhost:8080/api/lights`

### Get sensor device status

* **URL:** `/api/sensors`
* **Method:** `GET`
* **Description:** Gets a flattened list of all sensor readings on the local Matter network. Each reading is separated into its own object containing a composite ID (Node, Endpoint, and Sensor Name), the sensor name, and the raw integer value.
* **Example:** `http://localhost:8080/api/sensors`

### Commission a new Matter device

* **URL:** `/api/register`
* **Method:** `GET`
* **Description:** Initiates the commissioning process for a new device on the local network. Supports both auto-discovery and directed IP-based commissioning. The system automatically extracts the required PIN from the setup payload.
* **Parameters:**
  * `code` (string, required): The Matter setup payload code (e.g., an 11-digit manual pairing code or a QR code payload starting with `MT:`).
  * `ip` (string, optional): The IP address of the device for direct network commissioning, bypassing Bluetooth constraints.
* **Example:** `http://localhost:8080/api/register?code=11223344556&ip=192.168.1.100`

### Control a lighting device

* **URL:** `/api/set`
* **Method:** `GET` or `POST`
* **Description:** Controls the brightness and color temperature of a specific lighting device. Setting brightness to 0.0 automatically powers off the device.
* **Parameters / JSON Body:**
  * `id` (string, required): The target device identifier (e.g., "Node 1 - EP 8").
  * `brightness` (float, optional): The desired brightness level from 0.0 to 1.0.
  * `temperature` (integer, optional): The desired color temperature in Kelvin.
* **Example (GET):** `http://localhost:8080/api/set?id=Node%201%20-%20EP%208&brightness=0.8&temperature=4000`
* **Example (POST):** `curl -X POST -H "Content-Type: application/json" -d '{"id": "Node 1 - EP 8", "brightness": 0.8, "temperature": 4000}' http://localhost:8080/api/set`