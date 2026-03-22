"""MCP server for Matter Web Controller.

Connects to a running matter-srv HTTP server and exposes its
operations as MCP tools for LLM integration.

Usage:
    matter-mcp                        # connects to localhost:8080
    matter-mcp --host 192.168.1.10    # remote server
    matter-mcp --port 9090            # custom port
"""

import argparse
import json
import logging
import urllib.request
import urllib.error
import urllib.parse

from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

mcp = FastMCP("matter-webcontrol")

# Set via CLI args before mcp.run()
_base_url: str = "http://localhost:8080"


def _get(path: str, params: dict | None = None) -> dict | list:
    """HTTP GET to the matter-srv server."""
    url = f"{_base_url}{path}"
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(path: str, body: dict) -> dict | list:
    """HTTP POST JSON to the matter-srv server."""
    url = f"{_base_url}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Query tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_devices() -> list[dict]:
    """List all devices (physical and logical) with their current states and aliases."""
    return _get("/api/devices")


@mcp.tool()
def get_lights() -> list[dict]:
    """List lighting devices with normalized brightness (0.0-1.0) and color temperature (Kelvin)."""
    return _get("/api/lights")


@mcp.tool()
def get_sensors() -> list[dict]:
    """List sensor devices with their metrics (illuminance, temperature, humidity, occupancy, etc.)."""
    return _get("/api/sensors")


@mcp.tool()
def get_sensor(id: str) -> dict:
    """Get a single sensor's data by device ID or alias."""
    return _get("/api/sensor", {"id": id})


@mcp.tool()
def get_status() -> dict:
    """Quick summary: how many lights on/off, active sensors, connected bridges, total devices."""
    return _get("/api/status")


# ---------------------------------------------------------------------------
# Control tools
# ---------------------------------------------------------------------------

@mcp.tool()
def set_device(id: str, brightness: float | None = None, temperature: int | None = None) -> dict:
    """Control a device. Brightness: 0.0 (off) to 1.0 (full). Temperature: Kelvin (e.g. 4000)."""
    return _post("/api/set", {"id": id, "brightness": brightness, "temperature": temperature})


@mcp.tool()
def toggle(id: str) -> dict:
    """Toggle a device on or off. If on, turns off. If off, turns on at full brightness."""
    return _get("/api/toggle", {"id": id})


@mcp.tool()
def set_level(id: str, level: int) -> dict:
    """Set raw brightness level (0-254) for a device."""
    return _post("/api/level", {"id": id, "level": level})


@mcp.tool()
def set_mired(id: str, mireds: int) -> dict:
    """Set color temperature in mireds for a device."""
    return _post("/api/mired", {"id": id, "mireds": mireds})


@mcp.tool()
def batch_control(actions: list[dict]) -> list[dict]:
    """Control multiple devices at once. Each action: {"id": "...", "brightness": 0.5, "temperature": 4000}."""
    return _post("/api/batch", {"actions": actions})


# ---------------------------------------------------------------------------
# Management tools
# ---------------------------------------------------------------------------

@mcp.tool()
def set_name(id: str, name: str) -> dict:
    """Assign an alias to a device. Aliases can be used in place of device IDs."""
    return _post("/api/name", {"id": id, "name": name})


@mcp.tool()
def remove_name(id: str, name: str) -> dict:
    """Remove an alias from a device."""
    return _get("/api/name/remove", {"id": id, "name": name})


@mcp.tool()
def add_bridge(ip: str, port: int) -> dict:
    """Register a remote logical bridge by IP and port."""
    return _get("/api/bridge", {"ip": ip, "port": str(port)})


@mcp.tool()
def remove_bridge(ip: str, port: int) -> dict:
    """Remove a registered logical bridge."""
    return _get("/api/bridge/remove", {"ip": ip, "port": str(port)})


@mcp.tool()
def register_device(code: str, ip: str | None = None, name: str | None = None) -> dict:
    """Commission a new Matter device using its pairing code. Optionally specify IP and a name."""
    return _get("/api/register", {"code": code, "ip": ip, "name": name})


@mcp.tool()
def refresh() -> dict:
    """Force refresh all device states from Matter bridge and logical bridges."""
    return _get("/api/refresh")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    global _base_url

    parser = argparse.ArgumentParser(description="Matter MCP Server (HTTP client)")
    parser.add_argument("--host", type=str, default="localhost", help="Matter HTTP server host")
    parser.add_argument("--port", type=int, default=8080, help="Matter HTTP server port")
    args = parser.parse_args()

    _base_url = f"http://{args.host}:{args.port}"
    logging.info(f"MCP server connecting to {_base_url}")
    mcp.run()


if __name__ == "__main__":
    main()
