import argparse
import asyncio
import datetime
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

import chip.clusters.Objects as Clusters
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from cli.logic_bridge import LogicalBridgeManager
from cli.matter_bridge import MatterBridgeServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

bridge_instance: MatterBridgeServer = None
logical_manager = LogicalBridgeManager()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class NamePayload(BaseModel):
    id: str
    name: str

class ControlPayload(BaseModel):
    id: str
    brightness: Optional[float] = None
    temperature: Optional[int] = None

class LevelPayload(BaseModel):
    id: str
    level: Optional[int] = None

class MiredPayload(BaseModel):
    id: str
    mireds: Optional[int] = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SENSOR_KEYS = ["illuminance", "temperature", "pressure", "humidity", "occupancy", "contact"]


def _get_params(request: Request, payload, fields: list[str]) -> dict:
    """Extract parameters from POST payload or GET query string."""
    if request.method == "POST" and payload:
        return {f: getattr(payload, f, None) for f in fields}
    return {f: request.query_params.get(f) for f in fields}


def _parse_device_id(resolved_id: str) -> tuple[int, int]:
    """Look up node_id and endpoint_id from the device cache."""
    if bridge_instance:
        for dev in bridge_instance.cached_devices:
            if dev["id"] == resolved_id:
                return dev["node_id"], dev["endpoint_id"]
    raise HTTPException(status_code=404, detail="Physical device not found in cache")


def _find_device_state(resolved_id: str, key: str):
    """Search physical then logical devices for a state value."""
    if bridge_instance:
        for dev in bridge_instance.cached_devices:
            if dev["id"] == resolved_id and key in dev.get("states", {}):
                return dev["states"][key]

    for dev in logical_manager.get_all_devices().get("devices", []):
        if dev["id"] == resolved_id and key in dev.get("states", {}):
            return dev["states"][key]

    return None


def _find_logical_target(resolved_id: str):
    """Return (logical_device, client) or (None, None)."""
    for dev in logical_manager.get_all_devices().get("devices", []):
        if dev["id"] == resolved_id:
            client = logical_manager.registry.get(dev["node_id"])
            return dev, client
    return None, None


def _verify_hardware():
    if not bridge_instance or not bridge_instance.is_ready():
        raise HTTPException(status_code=503, detail="Server not ready for hardware control")


def _build_light_entry(device: dict, names: list) -> dict | None:
    """Build a lighting payload from a device dict, or None if not a light."""
    states = device.get("states", {})
    if "on_off" not in states and "brightness_raw" not in states:
        return None

    on = states.get("on_off")
    brightness = None
    if states.get("brightness_raw") is not None:
        brightness = round(max(0.0, min(1.0, states["brightness_raw"] / 254.0)), 2)
        if not on:
            brightness = 0.0

    entry = {"id": device["id"], "names": names, "state": on, "brightness": brightness}

    mireds = states.get("color_temp_mireds")
    if mireds and mireds > 0:
        entry["temperature"] = int(1_000_000 / mireds)

    return entry


def _build_sensor_entry(device: dict, names: list) -> dict | None:
    """Build a sensor payload from a device dict, or None if no sensor data."""
    states = device.get("states", {})
    data = {k: states[k] for k in SENSOR_KEYS if k in states}
    if not data:
        return None

    if "occupancy" in data:
        ts = bridge_instance.occupancy_history.get(device["id"]) if bridge_instance else None
        if ts:
            data["occupancy_last_active"] = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    return {"id": device["id"], "names": names, **data}


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bridge_instance
    port = getattr(app.state, "port", 8080)

    fabric_label = getattr(app.state, "fabric_label", None)

    bridge_instance = MatterBridgeServer(port)
    await bridge_instance.initialize(app, fabric_label=fabric_label)

    logical_manager.load_cache()

    if bridge_instance and bridge_instance.is_ready():
        bridge_instance._update_cache()

    count = logical_manager.refresh_bridges()
    logging.info(f"Startup sync complete. Refreshed Matter cache and {count} logical bridges.")

    yield

    await bridge_instance.shutdown(app)


app = FastAPI(title="Matter Web Controller", lifespan=lifespan)

# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/api/bridge")
async def add_bridge_api(ip: str, port: int):
    try:
        node_id = logical_manager.add_bridge(ip, port)
        return {"status": "success", "message": f"Registered logical bridge {node_id}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/devices")
async def get_devices_api():
    result = []

    if bridge_instance and bridge_instance.cached_devices:
        for dev in bridge_instance.cached_devices:
            copy = dict(dev)
            copy["states"] = dict(dev.get("states", {}))
            if copy["states"].get("color_temp_mireds") == 0:
                copy["states"].pop("color_temp_mireds", None)
            copy["names"] = bridge_instance.device_names.get(dev["id"], [])
            result.append(copy)

    result.extend(logical_manager.get_all_devices().get("devices", []))
    return result


@app.get("/api/lights")
async def get_lights_api():
    lights = []

    if bridge_instance:
        for dev in bridge_instance.cached_devices:
            entry = _build_light_entry(dev, bridge_instance.device_names.get(dev["id"], []))
            if entry:
                lights.append(entry)

    for dev in logical_manager.get_all_devices().get("devices", []):
        entry = _build_light_entry(dev, dev.get("names", []))
        if entry:
            lights.append(entry)

    return lights


@app.get("/api/sensors")
async def get_sensors_api():
    if not bridge_instance:
        return []

    sensors = []
    for dev in bridge_instance.cached_devices:
        entry = _build_sensor_entry(dev, bridge_instance.device_names.get(dev["id"], []))
        if entry:
            sensors.append(entry)
    return sensors


@app.get("/api/sensor")
async def get_sensor_api(id: str):
    resolved = bridge_instance.resolve_id(id)

    for dev in bridge_instance.cached_devices:
        if dev["id"] != resolved:
            continue

        entry = _build_sensor_entry(dev, bridge_instance.device_names.get(resolved, []))
        if entry:
            return entry
        raise HTTPException(status_code=404, detail="Device exists but contains no sensor clusters")

    raise HTTPException(status_code=404, detail="Sensor not found in cache")


@app.api_route("/api/name", methods=["GET", "POST"])
async def set_name_api(request: Request, payload: Optional[NamePayload] = None):
    params = _get_params(request, payload, ["id", "name"])
    device_id, new_name = params["id"], params["name"]

    if not device_id or not new_name:
        raise HTTPException(status_code=400, detail="Missing id or name parameter")

    resolved = bridge_instance.resolve_id(device_id)

    for existing_id, names in bridge_instance.device_names.items():
        if new_name in names and existing_id != resolved:
            raise HTTPException(status_code=409, detail="Name conflict: Alias already assigned to another device")

    bridge_instance.device_names.setdefault(resolved, [])
    if new_name not in bridge_instance.device_names[resolved]:
        bridge_instance.device_names[resolved].append(new_name)
        bridge_instance._save_names_cache()

    return {"status": "success", "id": resolved, "names": bridge_instance.device_names[resolved]}


def extract_matter_pin(setup_code: str) -> int:
    """Convert a Matter manual pairing code to a PIN."""
    clean = setup_code.replace("-", "").replace(" ", "")
    if len(clean) not in (11, 21) or not clean.isdigit():
        raise ValueError("Invalid manual pairing code format")
    return (int(clean[6:10]) << 14) | (int(clean[1:6]) & 0x3FFF)


@app.get("/api/register")
async def register_api(code: str, ip: Optional[str] = None, name: Optional[str] = None):
    _verify_hardware()

    try:
        if ip:
            pin = extract_matter_pin(code)
            await bridge_instance.client.send_command(
                "commission_on_network", setup_pin_code=pin, ip_address=ip
            )
        else:
            await bridge_instance.client.send_command("commission_with_code", code=code)
        return {"status": "success", "code": code, "ip": ip, "pending_name": name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.api_route("/api/set", methods=["GET", "POST"])
async def set_device_api(request: Request, payload: Optional[ControlPayload] = None):
    params = _get_params(request, payload, ["id", "brightness", "temperature"])
    device_id = params["id"]
    brightness_str = params["brightness"]
    temperature_str = params["temperature"]

    if not device_id:
        raise HTTPException(status_code=400, detail="Missing device id")

    resolved = bridge_instance.resolve_id(device_id)

    # Try logical bridge first
    target, client = _find_logical_target(resolved)
    if target:
        if not client:
            raise HTTPException(status_code=500, detail="Logical bridge client offline")
        try:
            if brightness_str is not None:
                level = int(max(0.0, min(1.0, float(brightness_str))) * 254)
                client.execute_event(target["endpoint_id"], "set_level", str(level))
            if temperature_str is not None:
                kelvin = int(temperature_str)
                if kelvin > 0:
                    client.execute_event(target["endpoint_id"], "set_color_temperature", str(int(1_000_000 / kelvin)))
            return {"status": "success", "id": resolved, "type": "logical"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Logical bridge execution failed: {e}")

    # Physical device
    _verify_hardware()
    node_id, endpoint_id = _parse_device_id(resolved)

    try:
        if brightness_str is not None:
            brightness = max(0.0, min(1.0, float(brightness_str)))
            if brightness == 0.0:
                cmd = Clusters.OnOff.Commands.Off()
            else:
                cmd = Clusters.LevelControl.Commands.MoveToLevelWithOnOff(
                    level=max(1, int(brightness * 254)), transitionTime=0
                )
            await bridge_instance.client.send_device_command(node_id, endpoint_id, cmd)

        if temperature_str is not None:
            kelvin = int(temperature_str)
            if kelvin > 0:
                cmd = Clusters.ColorControl.Commands.MoveToColorTemperature(
                    colorTemperatureMireds=int(1_000_000 / kelvin),
                    transitionTime=0, optionsMask=0, optionsOverride=0,
                )
                await bridge_instance.client.send_device_command(node_id, endpoint_id, cmd)

        return {"status": "success", "id": resolved, "type": "physical"}
    except Exception as e:
        logging.error(f"Command execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/subscribe")
async def subscribe_api(request: Request, id: str):
    """SSE stream for occupancy state changes."""
    resolved = bridge_instance.resolve_id(id)
    bridge_instance.occupancy_subscribers.setdefault(resolved, [])

    queue = asyncio.Queue()
    bridge_instance.occupancy_subscribers[resolved].append(queue)

    async def stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                state, ts = await queue.get()
                iso = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()
                yield f'data: {{"id": "{resolved}", "occupancy": {state}, "timestamp": "{iso}"}}\n\n'
        except asyncio.CancelledError:
            pass
        finally:
            subs = bridge_instance.occupancy_subscribers.get(resolved, [])
            if queue in subs:
                subs.remove(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/refresh")
async def refresh_api():
    matter_status = "skipped"
    if bridge_instance and bridge_instance.is_ready():
        try:
            bridge_instance._update_cache()
            matter_status = "success"
        except Exception as e:
            logging.error(f"Matter bridge refresh error: {e}")
            matter_status = "failed"

    try:
        count = logical_manager.refresh_bridges()
        return {"status": "success", "message": f"Refreshed {count} logical bridges. Matter: {matter_status}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.api_route("/api/level", methods=["GET", "POST"])
async def level_api(request: Request, payload: Optional[LevelPayload] = None):
    """Get or set raw brightness level (0-254)."""
    params = _get_params(request, payload, ["id", "level"])
    device_id, level_str = params["id"], params["level"]

    if not device_id:
        raise HTTPException(status_code=400, detail="Missing device id")

    resolved = bridge_instance.resolve_id(device_id)

    # GET mode
    if level_str is None:
        val = _find_device_state(resolved, "brightness_raw")
        if val is not None:
            return {"id": resolved, "level": val}
        raise HTTPException(status_code=404, detail="Device not found or level state unsupported")

    # SET mode
    level = max(0, min(254, int(level_str)))

    target, client = _find_logical_target(resolved)
    if target:
        if not client:
            raise HTTPException(status_code=500, detail="Logical bridge client offline")
        try:
            client.execute_event(target["endpoint_id"], "set_level", str(level))
            return {"status": "success", "id": resolved, "level": level, "type": "logical"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Logical bridge execution failed: {e}")

    _verify_hardware()
    node_id, endpoint_id = _parse_device_id(resolved)

    try:
        if level == 0:
            cmd = Clusters.OnOff.Commands.Off()
        else:
            cmd = Clusters.LevelControl.Commands.MoveToLevelWithOnOff(level=level, transitionTime=0)
        await bridge_instance.client.send_device_command(node_id, endpoint_id, cmd)
        return {"status": "success", "id": resolved, "level": level, "type": "physical"}
    except Exception as e:
        logging.error(f"Command execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.api_route("/api/mired", methods=["GET", "POST"])
async def mired_api(request: Request, payload: Optional[MiredPayload] = None):
    """Get or set color temperature in mireds."""
    params = _get_params(request, payload, ["id", "mireds"])
    device_id, mireds_str = params["id"], params["mireds"]

    if not device_id:
        raise HTTPException(status_code=400, detail="Missing device id")

    resolved = bridge_instance.resolve_id(device_id)

    # GET mode
    if mireds_str is None:
        val = _find_device_state(resolved, "color_temp_mireds")
        if val is not None:
            return {"id": resolved, "mireds": val}
        raise HTTPException(status_code=404, detail="Device not found or color temperature unsupported")

    # SET mode
    mireds = int(mireds_str)

    target, client = _find_logical_target(resolved)
    if target:
        if not client:
            raise HTTPException(status_code=500, detail="Logical bridge client offline")
        try:
            client.execute_event(target["endpoint_id"], "set_color_temperature", str(mireds))
            return {"status": "success", "id": resolved, "mireds": mireds, "type": "logical"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Logical bridge execution failed: {e}")

    _verify_hardware()
    node_id, endpoint_id = _parse_device_id(resolved)

    try:
        cmd = Clusters.ColorControl.Commands.MoveToColorTemperature(
            colorTemperatureMireds=mireds, transitionTime=0, optionsMask=0, optionsOverride=0,
        )
        await bridge_instance.client.send_device_command(node_id, endpoint_id, cmd)
        return {"status": "success", "id": resolved, "mireds": mireds, "type": "physical"}
    except Exception as e:
        logging.error(f"Command execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/metadata")
async def metadata_api(request: Request):
    host = request.url.hostname or "127.0.0.1"
    port = request.url.port or 8080
    base = f"http://{host}:{port}"

    all_devices = []
    if bridge_instance and bridge_instance.cached_devices:
        all_devices.extend(bridge_instance.cached_devices)
    all_devices.extend(logical_manager.get_all_devices().get("devices", []))

    metadata = []
    for dev in all_devices:
        dev_id = dev.get("id")
        if not dev_id:
            continue

        names = list(dev.get("names", []))
        if bridge_instance:
            for n in bridge_instance.device_names.get(dev_id, []):
                if n not in names:
                    names.append(n)

        name = names[0] if names else dev_id
        states = dev.get("states", {})

        has_on_off = "on_off" in states
        has_brightness = "brightness_raw" in states
        has_color_temp = "color_temp_mireds" in states
        has_occupancy = "occupancy" in states

        events = {}
        hw_type = "unknown"

        if has_occupancy:
            hw_type = "occupancy_sensor"
            events["read_occupancy"] = {
                "trigger": "occupancy_sensing_cluster",
                "script": (
                    f"import urllib.request, json\n"
                    f"response = urllib.request.urlopen('{base}/api/sensor?id={dev_id}')\n"
                    f"data = json.loads(response.read().decode('utf-8'))\n"
                    f"print(data.get('occupancy', 0))"
                ),
            }
            events["subscribe_occupancy"] = {
                "trigger": "occupancy_sse_stream",
                "script": (
                    f"import urllib.request\n"
                    f"response = urllib.request.urlopen('{base}/api/subscribe?id={dev_id}')\n"
                    f"for line in response:\n"
                    f"    print(line.decode('utf-8').strip())"
                ),
            }
        elif has_on_off:
            if has_color_temp:
                hw_type = "color_temperature_light"
            elif has_brightness:
                hw_type = "dimmable_light"
            else:
                hw_type = "on_off_light"

            events["turn_on"] = {
                "trigger": "on_off_cluster",
                "script": f"import urllib.request\nurllib.request.urlopen('{base}/api/set?id={dev_id}&brightness=1.0')",
            }
            events["turn_off"] = {
                "trigger": "on_off_cluster",
                "script": f"import urllib.request\nurllib.request.urlopen('{base}/api/set?id={dev_id}&brightness=0.0')",
            }

            if has_brightness or has_color_temp:
                events["set_level"] = {
                    "trigger": "level_control_cluster",
                    "script": (
                        f"import sys, urllib.request\n"
                        f"level = int(sys.argv[1]) if len(sys.argv) > 1 else 254\n"
                        f"urllib.request.urlopen(f'{base}/api/level?id={dev_id}&level={{level}}')"
                    ),
                }
                events["read_level"] = {
                    "trigger": "level_control_cluster",
                    "script": (
                        f"import urllib.request, json\n"
                        f"try:\n"
                        f"    response = urllib.request.urlopen('{base}/api/level?id={dev_id}')\n"
                        f"    data = json.loads(response.read().decode('utf-8'))\n"
                        f"    print(data.get('level', 0))\n"
                        f"except Exception:\n"
                        f"    print(0)"
                    ),
                }

            if has_color_temp:
                events["set_color_temperature"] = {
                    "trigger": "color_control_cluster",
                    "script": (
                        f"import sys, urllib.request\n"
                        f"mireds = int(sys.argv[1]) if len(sys.argv) > 1 else 250\n"
                        f"urllib.request.urlopen(f'{base}/api/mired?id={dev_id}&mireds={{mireds}}')"
                    ),
                }
                events["read_color_temperature"] = {
                    "trigger": "color_control_cluster",
                    "script": (
                        f"import urllib.request, json\n"
                        f"try:\n"
                        f"    response = urllib.request.urlopen('{base}/api/mired?id={dev_id}')\n"
                        f"    data = json.loads(response.read().decode('utf-8'))\n"
                        f"    print(data.get('mireds', 0))\n"
                        f"except Exception:\n"
                        f"    print(0)"
                    ),
                }

        if hw_type != "unknown":
            metadata.append({
                "node_id": dev_id,
                "name": name,
                "hardware_type": hw_type,
                "events": events,
            })

    return {
        "bridge": {
            "id": "matter_bridge_http",
            "type": "lighting_controller",
            "network_host": host,
            "network_port": port,
        },
        "devices": metadata,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Matter API Web Server")
    parser.add_argument("--port", type=int, default=8080, help="Web server port")
    parser.add_argument("--fabric", type=str, default=None, help="Matter fabric label")
    args = parser.parse_args()

    app.state.port = args.port
    app.state.fabric_label = args.fabric
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
