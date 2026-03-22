"""MCP server for Matter Web Controller.

Exposes device query and control as MCP tools so LLMs can interact
with Matter smart home devices directly.
"""

import argparse
import asyncio
import logging

from mcp.server.fastmcp import FastMCP

from cli.core import DeviceController
from cli.logic_bridge import LogicalBridgeManager
from cli.matter_bridge import MatterBridgeServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

mcp = FastMCP("matter-webcontrol")
controller: DeviceController = None


async def _init_controller(port: int = 8080, fabric_label: str | None = None):
    global controller
    if controller is not None:
        return

    bridge = MatterBridgeServer(port)

    class FakeApp:
        pass

    await bridge.initialize(FakeApp(), fabric_label=fabric_label)

    logical = LogicalBridgeManager()
    logical.load_cache()

    controller = DeviceController(bridge, logical)

    if bridge.is_ready():
        bridge._update_cache()

    count = logical.refresh_bridges()
    logging.info(f"MCP startup complete. Refreshed Matter cache and {count} logical bridges.")


# ---------------------------------------------------------------------------
# Query tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_devices() -> list[dict]:
    """List all devices (physical and logical) with their current states and aliases."""
    await _init_controller()
    return controller.get_devices()


@mcp.tool()
async def get_lights() -> list[dict]:
    """List lighting devices with normalized brightness (0.0-1.0) and color temperature (Kelvin)."""
    await _init_controller()
    return controller.get_lights()


@mcp.tool()
async def get_sensors() -> list[dict]:
    """List sensor devices with their metrics (illuminance, temperature, humidity, occupancy, etc.)."""
    await _init_controller()
    return controller.get_sensors()


@mcp.tool()
async def get_sensor(id: str) -> dict:
    """Get a single sensor's data by device ID or alias."""
    await _init_controller()
    return controller.get_sensor(id)


@mcp.tool()
async def get_status() -> dict:
    """Quick summary: how many lights on/off, active sensors, connected bridges, total devices."""
    await _init_controller()
    return controller.get_status()


# ---------------------------------------------------------------------------
# Control tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def set_device(id: str, brightness: float | None = None, temperature: int | None = None) -> dict:
    """Control a device. Brightness: 0.0 (off) to 1.0 (full). Temperature: Kelvin (e.g. 4000)."""
    await _init_controller()
    return await controller.set_device(id, brightness, temperature)


@mcp.tool()
async def toggle(id: str) -> dict:
    """Toggle a device on or off. If on, turns off. If off, turns on at full brightness."""
    await _init_controller()
    return await controller.toggle(id)


@mcp.tool()
async def set_level(id: str, level: int) -> dict:
    """Set raw brightness level (0-254) for a device."""
    await _init_controller()
    return await controller.set_level(id, level)


@mcp.tool()
async def set_mired(id: str, mireds: int) -> dict:
    """Set color temperature in mireds for a device."""
    await _init_controller()
    return await controller.set_mired(id, mireds)


@mcp.tool()
async def batch_control(actions: list[dict]) -> list[dict]:
    """Control multiple devices at once. Each action: {"id": "...", "brightness": 0.5, "temperature": 4000}."""
    await _init_controller()
    return await controller.batch_control(actions)


# ---------------------------------------------------------------------------
# Management tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def set_name(id: str, name: str) -> dict:
    """Assign an alias to a device. Aliases can be used in place of device IDs."""
    await _init_controller()
    return controller.set_name(id, name)


@mcp.tool()
async def remove_name(id: str, name: str) -> dict:
    """Remove an alias from a device."""
    await _init_controller()
    return controller.remove_name(id, name)


@mcp.tool()
async def add_bridge(ip: str, port: int) -> dict:
    """Register a remote logical bridge by IP and port."""
    await _init_controller()
    return controller.add_bridge(ip, port)


@mcp.tool()
async def remove_bridge(ip: str, port: int) -> dict:
    """Remove a registered logical bridge."""
    await _init_controller()
    return controller.remove_bridge(ip, port)


@mcp.tool()
async def register_device(code: str, ip: str | None = None, name: str | None = None) -> dict:
    """Commission a new Matter device using its pairing code. Optionally specify IP and a name."""
    await _init_controller()
    return await controller.register_device(code, ip, name)


@mcp.tool()
async def refresh() -> dict:
    """Force refresh all device states from Matter bridge and logical bridges."""
    await _init_controller()
    return controller.refresh()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Matter MCP Server")
    parser.add_argument("--port", type=int, default=8080, help="Matter bridge port")
    parser.add_argument("--fabric", type=str, default=None, help="Matter fabric label")
    args = parser.parse_args()

    async def init():
        await _init_controller(port=args.port, fabric_label=args.fabric)

    asyncio.get_event_loop().run_until_complete(init())
    mcp.run()


if __name__ == "__main__":
    main()
