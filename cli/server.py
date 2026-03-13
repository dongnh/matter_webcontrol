import argparse
import asyncio
import logging
import os
import time
import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional
import uvicorn
import chip.clusters.Objects as Clusters
from cli.matter_bridge import MatterBridgeServer
from cli.logic_bridge import LogicalBridgeManager

# Configure standard logging protocol
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

bridge_instance: MatterBridgeServer = None
logical_manager = LogicalBridgeManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages the initialization and termination of hardware and logical subprocesses."""
    global bridge_instance
    port = getattr(app.state, "port", 8080)
    
    # Initialize physical Matter bridge
    bridge_instance = MatterBridgeServer(port)
    await bridge_instance.initialize(app)
    
    # Initialize logical bridges from persistent storage
    logical_manager.load_cache()
    
    # Execute comprehensive state refresh during startup
    if bridge_instance and bridge_instance.is_ready():
        bridge_instance._update_cache()
        
    updated_logical_count = logical_manager.refresh_bridges()
    logging.info(f"Startup synchronization complete. Refreshed Matter cache and {updated_logical_count} logical bridges.")
    
    yield
    
    # Execute graceful shutdown
    await bridge_instance.shutdown(app)

app = FastAPI(title="Matter Web Controller", lifespan=lifespan)

class NamePayload(BaseModel):
    id: str
    name: str

class ControlPayload(BaseModel):
    id: str
    brightness: Optional[float] = None
    temperature: Optional[int] = None

def verify_hardware_readiness():
    """Validates operational context prior to executing physical hardware state mutations."""
    if not bridge_instance or not bridge_instance.is_ready():
        raise HTTPException(status_code=503, detail="Server not ready for hardware control")

@app.get("/api/bridge")
async def add_logical_bridge_api(ip: str, port: int):
    """Registers a new logical bridge and persists it to the configuration cache."""
    try:
        node_id = logical_manager.add_bridge(ip, port)
        return {"status": "success", "message": f"Registered logical bridge {node_id}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/devices")
async def serve_all_devices_api():
    """Retrieves identical cache representation of the current local and logical device states."""
    response_data = []
    
    if bridge_instance and bridge_instance.cached_devices:
        for device in bridge_instance.cached_devices:
            # Create a deep copy to prevent mutating the internal cache
            dev_copy = dict(device)
            dev_copy["states"] = dict(device.get("states", {}))
            
            # Purge invalid hardware temperature values
            if dev_copy["states"].get("color_temp_mireds") == 0:
                dev_copy["states"].pop("color_temp_mireds", None)
                
            dev_copy["names"] = bridge_instance.device_names.get(device["id"], [])
            response_data.append(dev_copy)
            
    logical_data = logical_manager.get_all_devices().get("devices", [])
    response_data.extend(logical_data)
    
    return response_data

@app.get("/api/lights")
async def serve_lighting_api():
    """Filters and scales hardware attributes strictly relevant to lighting nodes."""
    lighting_devices = []
    
    if bridge_instance:
        for device in bridge_instance.cached_devices:
            states = device.get("states", {})
            if "on_off" in states or "brightness_raw" in states:
                state = states.get("on_off")
                mapped_brightness = None
                if "brightness_raw" in states and states["brightness_raw"] is not None:
                    mapped_brightness = round(max(0.0, min(1.0, states["brightness_raw"] / 254.0)), 2)
                    if not state:
                        mapped_brightness = 0.0
                
                device_payload = {
                    "id": device["id"],
                    "names": bridge_instance.device_names.get(device["id"], []),
                    "state": state,
                    "brightness": mapped_brightness
                }
                
                # Conditionally append physical temperature
                if "color_temp_mireds" in states and states["color_temp_mireds"]:
                    device_payload["temperature"] = int(1000000 / states["color_temp_mireds"])
                    
                lighting_devices.append(device_payload)
                
    logical_data = logical_manager.get_all_devices().get("devices", [])
    for device in logical_data:
        states = device.get("states", {})
        if "on_off" in states or "brightness_raw" in states:
            state = states.get("on_off")
            mapped_brightness = None
            if "brightness_raw" in states and states["brightness_raw"] is not None:
                mapped_brightness = round(max(0.0, min(1.0, states["brightness_raw"] / 254.0)), 2)
                if not state:
                    mapped_brightness = 0.0
                    
            device_payload = {
                "id": device["id"],
                "names": device.get("names", []),
                "state": state,
                "brightness": mapped_brightness
            }
            
            # Conditionally append logical temperature
            if "color_temp_mireds" in states and states["color_temp_mireds"] > 0:
                device_payload["temperature"] = int(1000000 / states["color_temp_mireds"])
                    
            lighting_devices.append(device_payload)
            
    return lighting_devices

@app.get("/api/sensors")
async def serve_sensors_api():
    """Aggregates logical sensory metrics combined with occupancy timestamp history."""
    sensors_data = []
    if not bridge_instance:
        return sensors_data
        
    sensor_keys = ["illuminance", "temperature", "pressure", "humidity", "occupancy", "contact"]
    for device in bridge_instance.cached_devices:
        states = device.get("states", {})
        sensor_payload = {}
        for key in sensor_keys:
            if key in states:
                sensor_payload[key] = states[key]
                
        if sensor_payload:
            if "occupancy" in sensor_payload:
                last_active = bridge_instance.occupancy_history.get(device["id"])
                if last_active:
                    readable_time = datetime.datetime.fromtimestamp(last_active).strftime('%Y-%m-%d %H:%M:%S')
                    sensor_payload["occupancy_last_active"] = readable_time

            sensors_data.append({
                "id": device["id"],
                "names": bridge_instance.device_names.get(device["id"], []),
                **sensor_payload
            })
    return sensors_data

@app.get("/api/sensor")
async def serve_single_sensor_api(id: str):
    """Isolates specific sensor entity data targeting a strictly defined identifier."""
    resolved_id = bridge_instance.resolve_id(id)
    sensor_keys = ["illuminance", "temperature", "pressure", "humidity", "occupancy", "contact"]

    for device in bridge_instance.cached_devices:
        if device.get("id") == resolved_id:
            states = device.get("states", {})
            sensor_payload = {}
            for key in sensor_keys:
                if key in states:
                    sensor_payload[key] = states[key]
                    
            if sensor_payload:
                if "occupancy" in sensor_payload:
                    last_active = bridge_instance.occupancy_history.get(resolved_id)
                    if last_active:
                        readable_time = datetime.datetime.fromtimestamp(last_active).strftime('%Y-%m-%d %H:%M:%S')
                        sensor_payload["occupancy_last_active"] = readable_time

                return {
                    "id": resolved_id,
                    "names": bridge_instance.device_names.get(resolved_id, []),
                    **sensor_payload
                }
            raise HTTPException(status_code=404, detail="Device exists but contains no sensor clusters")
    raise HTTPException(status_code=404, detail="Sensor not found in cache")

@app.api_route("/api/name", methods=["GET", "POST"])
async def serve_name_api(request: Request, payload: Optional[NamePayload] = None):
    """Enforces constraint uniqueness when resolving logic names into physical hardware IDs."""
    if request.method == "POST" and payload:
        device_id = payload.id
        new_name = payload.name
    else:
        device_id = request.query_params.get("id")
        new_name = request.query_params.get("name")

    if not device_id or not new_name:
        raise HTTPException(status_code=400, detail="Missing id or name parameter")
        
    resolved_id = bridge_instance.resolve_id(device_id)
    
    for existing_id, names in bridge_instance.device_names.items():
        if new_name in names and existing_id != resolved_id:
            raise HTTPException(status_code=409, detail="Name conflict: Alias already assigned to another device")
    
    if resolved_id not in bridge_instance.device_names:
        bridge_instance.device_names[resolved_id] = []
        
    if new_name not in bridge_instance.device_names[resolved_id]:
        bridge_instance.device_names[resolved_id].append(new_name)
        bridge_instance._save_names_cache()
        
    return {"status": "success", "id": resolved_id, "names": bridge_instance.device_names[resolved_id]}

def extract_matter_pin(setup_code: str) -> int:
    """Computes hexadecimal conversion bounds targeting conventional manual pairing structures."""
    clean_code = setup_code.replace("-", "").replace(" ", "")
    if len(clean_code) not in (11, 21) or not clean_code.isdigit():
        raise ValueError("Invalid manual pairing code format")
    value_2 = int(clean_code[1:6])
    value_3 = int(clean_code[6:10])
    return (value_3 << 14) | (value_2 & 0x3FFF)

@app.get("/api/register")
async def serve_commission_api(code: str, ip: Optional[str] = None, name: Optional[str] = None):
    """Executes blocking network inclusion routines."""
    verify_hardware_readiness()
    
    try:
        if ip:
            pin_code = extract_matter_pin(code)
            await bridge_instance.client.send_command(
                "commission_on_network", 
                setup_pin_code=pin_code, 
                ip_address=ip
            )
        else:
            await bridge_instance.client.send_command(
                "commission_with_code", 
                code=code
            )
        return {"status": "success", "code": code, "ip": ip, "pending_name": name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.api_route("/api/set", methods=["GET", "POST"])
async def serve_set_api(request: Request, payload: Optional[ControlPayload] = None):
    """Dispatches physical actuation requests parsed mathematically against Matter clusters or Logical scripts."""
    if request.method == "POST" and payload:
        device_id = payload.id
        brightness_str = payload.brightness
        temperature_str = payload.temperature
    else:
        device_id = request.query_params.get("id")
        brightness_str = request.query_params.get("brightness")
        temperature_str = request.query_params.get("temperature")

    if not device_id:
        raise HTTPException(status_code=400, detail="Missing device id")

    resolved_id = bridge_instance.resolve_id(device_id)

    logical_devices = logical_manager.get_all_devices().get("devices", [])
    logical_target = next((d for d in logical_devices if d["id"] == resolved_id), None)

    if logical_target:
        node_id = logical_target["node_id"]
        endpoint_id = logical_target["endpoint_id"]
        client = logical_manager.registry.get(node_id)
        
        if client:
            try:
                if brightness_str is not None:
                    brightness_val = float(brightness_str)
                    clamped_brightness = max(0.0, min(1.0, brightness_val))
                    logical_level = int(clamped_brightness * 254)
                    client.execute_event(endpoint_id, "set_level", str(logical_level))
                
                if temperature_str is not None:
                    temp_kelvin = int(temperature_str)
                    if temp_kelvin > 0:
                        mireds = int(1000000 / temp_kelvin)
                        client.execute_event(endpoint_id, "set_color_temperature", str(mireds))
                        
                return {"status": "success", "id": resolved_id, "type": "logical"}
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Logical bridge execution failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Logical bridge client offline")

    verify_hardware_readiness()

    try:
        parts = resolved_id.replace("dev_", "").split("_")
        node_id = int(parts[0])
        endpoint_id = int(parts[1])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    try:
        if brightness_str is not None:
            brightness = float(brightness_str)
            brightness = max(0.0, min(1.0, brightness))
            
            if brightness == 0.0:
                cmd = Clusters.OnOff.Commands.Off()
                await bridge_instance.client.send_device_command(node_id, endpoint_id, cmd)
            else:
                level = max(1, int(brightness * 254))
                cmd = Clusters.LevelControl.Commands.MoveToLevelWithOnOff(level=level, transitionTime=0)
                await bridge_instance.client.send_device_command(node_id, endpoint_id, cmd)

        if temperature_str is not None:
            temp_kelvin = int(temperature_str)
            if temp_kelvin > 0:
                mireds = int(1000000 / temp_kelvin)
                cmd = Clusters.ColorControl.Commands.MoveToColorTemperature(
                    colorTemperatureMireds=mireds,
                    transitionTime=0,
                    optionsMask=0,
                    optionsOverride=0
                )
                await bridge_instance.client.send_device_command(node_id, endpoint_id, cmd)

        return {"status": "success", "id": resolved_id, "type": "physical"}
    except Exception as e:
        logging.error(f"Command execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/subscribe")
async def subscribe_api(request: Request, id: str):
    """Establishes a Server-Sent Events stream for occupancy state mutations."""
    resolved_id = bridge_instance.resolve_id(id)

    if resolved_id not in bridge_instance.occupancy_subscribers:
        bridge_instance.occupancy_subscribers[resolved_id] = []

    client_queue = asyncio.Queue()
    bridge_instance.occupancy_subscribers[resolved_id].append(client_queue)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                
                # Unpack state and timestamp from the queue
                occupancy_state, timestamp = await client_queue.get()
                
                # Convert UNIX timestamp to ISO 8601 UTC format
                iso_time = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).isoformat()
                
                yield f"data: {{\"id\": \"{resolved_id}\", \"occupancy\": {occupancy_state}, \"timestamp\": \"{iso_time}\"}}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if client_queue in bridge_instance.occupancy_subscribers.get(resolved_id, []):
                bridge_instance.occupancy_subscribers[resolved_id].remove(client_queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/refresh")
async def refresh_all_bridges_api():
    # Force a synchronized state refresh for both physical and logical networks
    matter_status = "skipped"
    
    # 1. Refresh physical Matter devices
    if bridge_instance and bridge_instance.is_ready():
        try:
            bridge_instance._update_cache()
            matter_status = "success"
        except Exception as e:
            logging.error(f"Matter bridge refresh error: {e}")
            matter_status = "failed"

    # 2. Refresh logical bridges
    try:
        updated_count = logical_manager.refresh_bridges()
        return {
            "status": "success", 
            "message": f"Refreshed metadata for {updated_count} logical bridges. Matter bridge status: {matter_status}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/metadata")
async def serve_metadata_api(request: Request):
    # Extracts network context for dynamic URL generation
    host = request.url.hostname or "127.0.0.1"
    port = request.url.port or 8080
    base_url = f"http://{host}:{port}"
    
    devices_metadata = []
    all_devices = []
    
    # Append physical Matter devices directly managed by this server
    if bridge_instance and bridge_instance.cached_devices:
        all_devices.extend(bridge_instance.cached_devices)
        
    # Append external devices from logical bridges
    logical_data = logical_manager.get_all_devices().get("devices", [])
    all_devices.extend(logical_data)
    
    for device in all_devices:
        dev_id = device.get("id")
        if not dev_id:
            continue
            
        # Extract names from the device object (logical) and physical cache
        available_names = device.get("names", [])
        if bridge_instance and dev_id in bridge_instance.device_names:
            for n in bridge_instance.device_names.get(dev_id, []):
                if n not in available_names:
                    available_names.append(n)
            
        name = available_names[0] if available_names else dev_id
        states = device.get("states", {})
        
        events = {}
        hardware_type = "unknown"
        
        has_on_off = "on_off" in states
        has_brightness = "brightness_raw" in states
        has_color_temp = "color_temp_mireds" in states
        has_occupancy = "occupancy" in states
        
        if has_occupancy:
            hardware_type = "occupancy_sensor"
            events["read_occupancy"] = {
                "trigger": "occupancy_sensing_cluster",
                "script": f"import urllib.request, json\nresponse = urllib.request.urlopen('{base_url}/api/sensor?id={dev_id}')\ndata = json.loads(response.read().decode('utf-8'))\nprint(data.get('occupancy', 0))"
            }
            events["subscribe_occupancy"] = {
                "trigger": "occupancy_sse_stream",
                "script": f"import urllib.request\nresponse = urllib.request.urlopen('{base_url}/api/subscribe?id={dev_id}')\nfor line in response:\n    print(line.decode('utf-8').strip())"
            }
        elif has_on_off:
            if has_color_temp:
                hardware_type = "color_temperature_light"
            elif has_brightness:
                hardware_type = "dimmable_light"
            else:
                hardware_type = "on_off_light"
                
            events["turn_on"] = {
                "trigger": "on_off_cluster",
                "script": f"import urllib.request\nurllib.request.urlopen('{base_url}/api/set?id={dev_id}&brightness=1.0')"
            }
            events["turn_off"] = {
                "trigger": "on_off_cluster",
                "script": f"import urllib.request\nurllib.request.urlopen('{base_url}/api/set?id={dev_id}&brightness=0.0')"
            }
            
            if has_brightness or has_color_temp:
                events["set_level"] = {
                    "trigger": "level_control_cluster",
                    "script": f"import sys, urllib.request\nlevel = int(sys.argv[1]) if len(sys.argv) > 1 else 254\nurllib.request.urlopen(f'{base_url}/api/level?id={dev_id}&level={{level}}')"
                }
                events["read_level"] = {
                    "trigger": "level_control_cluster",
                    "script": f"import urllib.request, json\ntry:\n    response = urllib.request.urlopen('{base_url}/api/level?id={dev_id}')\n    data = json.loads(response.read().decode('utf-8'))\n    print(data.get('level', 0))\nexcept Exception:\n    print(0)"
                }
                
            if has_color_temp:
                events["set_color_temperature"] = {
                    "trigger": "color_control_cluster",
                    "script": f"import sys, urllib.request\nmireds = int(sys.argv[1]) if len(sys.argv) > 1 else 250\nurllib.request.urlopen(f'{base_url}/api/mired?id={dev_id}&mireds={{mireds}}')"
                }
                events["read_color_temperature"] = {
                    "trigger": "color_control_cluster",
                    "script": f"import urllib.request, json\ntry:\n    response = urllib.request.urlopen('{base_url}/api/mired?id={dev_id}')\n    data = json.loads(response.read().decode('utf-8'))\n    print(data.get('mireds', 0))\nexcept Exception:\n    print(0)"
                }
                
        if hardware_type != "unknown":
            devices_metadata.append({
                "node_id": dev_id,
                "name": name,
                "hardware_type": hardware_type,
                "events": events
            })

    return {
        "bridge": {
            "id": "matter_bridge_http",
            "type": "lighting_controller",
            "network_host": host,
            "network_port": port
        },
        "devices": devices_metadata
    }

class LevelPayload(BaseModel):
    id: str
    level: Optional[int] = None

class MiredPayload(BaseModel):
    id: str
    mireds: Optional[int] = None

@app.api_route("/api/level", methods=["GET", "POST"])
async def serve_level_api(request: Request, payload: Optional[LevelPayload] = None):
    """Retrieves or mutates the raw brightness level (0-254) of a specific node."""
    if request.method == "POST" and payload:
        device_id = payload.id
        level_str = payload.level
    else:
        device_id = request.query_params.get("id")
        level_str = request.query_params.get("level")

    if not device_id:
        raise HTTPException(status_code=400, detail="Missing device id")

    resolved_id = bridge_instance.resolve_id(device_id)

    # Accessor Mode (Getter)
    if level_str is None:
        for device in bridge_instance.cached_devices:
            if device["id"] == resolved_id:
                states = device.get("states", {})
                if "brightness_raw" in states:
                    return {"id": resolved_id, "level": states["brightness_raw"]}
                    
        logical_devices = logical_manager.get_all_devices().get("devices", [])
        for device in logical_devices:
            if device["id"] == resolved_id:
                states = device.get("states", {})
                if "brightness_raw" in states:
                    return {"id": resolved_id, "level": states["brightness_raw"]}
                    
        raise HTTPException(status_code=404, detail="Device not found or level state unsupported")

    # Mutator Mode (Setter)
    level_val = max(0, min(254, int(level_str)))

    logical_devices = logical_manager.get_all_devices().get("devices", [])
    logical_target = next((d for d in logical_devices if d["id"] == resolved_id), None)

    if logical_target:
        node_id = logical_target["node_id"]
        endpoint_id = logical_target["endpoint_id"]
        client = logical_manager.registry.get(node_id)
        if client:
            try:
                client.execute_event(endpoint_id, "set_level", str(level_val))
                return {"status": "success", "id": resolved_id, "level": level_val, "type": "logical"}
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Logical bridge execution failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Logical bridge client offline")

    verify_hardware_readiness()
    try:
        parts = resolved_id.replace("dev_", "").split("_")
        node_id = int(parts[0])
        endpoint_id = int(parts[1])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    try:
        if level_val == 0:
            cmd = Clusters.OnOff.Commands.Off()
        else:
            cmd = Clusters.LevelControl.Commands.MoveToLevelWithOnOff(level=level_val, transitionTime=0)
        await bridge_instance.client.send_device_command(node_id, endpoint_id, cmd)
        return {"status": "success", "id": resolved_id, "level": level_val, "type": "physical"}
    except Exception as e:
        logging.error(f"Command execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.api_route("/api/mired", methods=["GET", "POST"])
async def serve_mired_api(request: Request, payload: Optional[MiredPayload] = None):
    """Retrieves or mutates the color temperature (mireds) of a specific node."""
    if request.method == "POST" and payload:
        device_id = payload.id
        mireds_str = payload.mireds
    else:
        device_id = request.query_params.get("id")
        mireds_str = request.query_params.get("mireds")

    if not device_id:
        raise HTTPException(status_code=400, detail="Missing device id")

    resolved_id = bridge_instance.resolve_id(device_id)

    # Accessor Mode (Getter)
    if mireds_str is None:
        for device in bridge_instance.cached_devices:
            if device["id"] == resolved_id:
                states = device.get("states", {})
                if "color_temp_mireds" in states:
                    return {"id": resolved_id, "mireds": states["color_temp_mireds"]}
                    
        logical_devices = logical_manager.get_all_devices().get("devices", [])
        for device in logical_devices:
            if device["id"] == resolved_id:
                states = device.get("states", {})
                if "color_temp_mireds" in states:
                    return {"id": resolved_id, "mireds": states["color_temp_mireds"]}
                    
        raise HTTPException(status_code=404, detail="Device not found or color temperature unsupported")

    # Mutator Mode (Setter)
    mireds_val = int(mireds_str)

    logical_devices = logical_manager.get_all_devices().get("devices", [])
    logical_target = next((d for d in logical_devices if d["id"] == resolved_id), None)

    if logical_target:
        node_id = logical_target["node_id"]
        endpoint_id = logical_target["endpoint_id"]
        client = logical_manager.registry.get(node_id)
        if client:
            try:
                client.execute_event(endpoint_id, "set_color_temperature", str(mireds_val))
                return {"status": "success", "id": resolved_id, "mireds": mireds_val, "type": "logical"}
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Logical bridge execution failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Logical bridge client offline")

    verify_hardware_readiness()
    try:
        parts = resolved_id.replace("dev_", "").split("_")
        node_id = int(parts[0])
        endpoint_id = int(parts[1])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    try:
        cmd = Clusters.ColorControl.Commands.MoveToColorTemperature(
            colorTemperatureMireds=mireds_val,
            transitionTime=0,
            optionsMask=0,
            optionsOverride=0
        )
        await bridge_instance.client.send_device_command(node_id, endpoint_id, cmd)
        return {"status": "success", "id": resolved_id, "mireds": mireds_val, "type": "physical"}
    except Exception as e:
        logging.error(f"Command execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def main():
    parser = argparse.ArgumentParser(description="Matter API Web Server")
    parser.add_argument("--port", type=int, default=8080, help="Web server port")
    args = parser.parse_args()
    
    app.state.port = args.port
    uvicorn.run(app, host="0.0.0.0", port=args.port)

if __name__ == "__main__":
    main()