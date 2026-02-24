# Matter Web Controller

This document explains how the local Matter device management system works using a web interface.

## System Architecture

The system separates background tasks and simplifies network rules to make it easier to use:

1. **Background Process:** The app runs the standard `python-matter-server` as a separate background task. This keeps the heavy work away from the web server.
2. **WebSockets Connection:** The web server keeps a constant connection to the background task. This updates the network data in real time without slowing things down.
3. **HTTP Setup:** The web server acts as a middleman. It changes complex WebSocket data into a simple HTTP GET link. Users only need to visit a simple web address to get JSON data.

## Requirements

You need Python 3.10 or newer.

## Installation

Create a virtual environment and install the package using your package manager. This will automatically install required tools like `aiohttp` and `home-assistant-chip-core`.

## How to Run

Start the system by typing the executable command `matter-server-start`. You can use the `--port` parameter to set the web server port. The default is 8080. The background Matter server will automatically use the next port number.

## API Endpoints

### Get lighting device status

* **URL:** `/api/lights`
* **Method:** `GET`
* **Description:** Gets a list of all devices that have lighting features on the local Matter network. The data includes the Node ID, Endpoint ID, power state, and current brightness level.
* **Example:** `http://localhost:8080/api/lights`