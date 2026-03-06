import argparse
import logging
import os
import time
import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
import uvicorn
import chip.clusters.Objects as Clusters
from cli.matter_bridge import MatterBridgeServer
from cli.logic_bridge import LogicalBridgeManager

# Configure standard logging protocol
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

bridge_instance: MatterBridgeServer = None
# Initialize the global logical bridge manager
logical_manager = LogicalBridgeManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages the initialization and termination of hardware and logical subprocesses."""
    global bridge_instance
    port = getattr(app.state, "port", 8080)
    bridge_instance = MatterBridgeServer(port)
    await bridge_instance.initialize(app)
    
    # Execute automated state restoration and verify logical bridge accessibility
    logical_manager.load_cache()
    
    yield
    await bridge_instance.shutdown(app)

app = FastAPI(title="Unified Matter and Logical API Bridge", lifespan=lifespan)

# --- Rigid Data Validation Models ---

class NamePayload(BaseModel):
    id: str
    name: str

class ControlPayload(BaseModel):
    id: str
    brightness: Optional[float] = None
    temperature: Optional[int] = None

class CallbackPayload(BaseModel):
    id: str
    script: str

# --- Operational Guards ---

def verify_hardware_readiness():
    """Validates operational context prior to executing physical hardware state mutations."""
    if not bridge_instance or not bridge_instance.is_ready():
        raise HTTPException(status_code=503, detail="Server not ready for hardware control")

# --- Logical Bridge Integration Endpoints ---

@app.get("/api/bridge")
async def add_logical_bridge_api(ip: str, port: int):
    """Registers a new logical bridge and persists it to the configuration cache."""
    try:
        node_id = logical_manager.add_bridge(ip, port)
        return {"status": "success", "message": f"Registered logical bridge {node_id}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# --- Routing Protocol Endpoints ---

@app.get("/api/devices")
async def serve_all_devices_api():
    """Retrieves identical cache representation of the current local and logical device states."""
    response_data = []
    
    # Append physical Matter devices
    if bridge_instance and bridge_instance.cached_devices:
        for device in bridge_instance.cached_devices:
            dev_copy = dict(device)
            dev_copy["names"] = bridge_instance.device_names.get(device["id"], [])
            response_data.append(dev_copy)
            
    # Append logical devices
    logical_data = logical_manager.get_all_devices().get("devices", [])
    response_data.extend(logical_data)
    
    return response_data

@app.get("/api/lights")
async def serve_lighting_api():
    """Filters and scales hardware attributes strictly relevant to lighting nodes."""
    lighting_devices = []
    
    # Process physical devices
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
                
                color_temp_kelvin = None
                if "color_temp_mireds" in states and states["color_temp_mireds"] is not None and states["color_temp_mireds"] > 0:
                    color_temp_kelvin = int(1000000 / states["color_temp_mireds"])
                    
                lighting_devices.append({
                    "id": device["id"],
                    "names": bridge_instance.device_names.get(device["id"], []),
                    "state": state,
                    "brightness": mapped_brightness,
                    "temperature": color_temp_kelvin
                })
                
    # Process logical devices
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
                    
            lighting_devices.append({
                "id": device["id"],
                "names": device.get("names", []),
                "state": state,
                "brightness": mapped_brightness,
                "temperature": None 
            })
            
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
                if device["id"] in bridge_instance.occupancy_callbacks:
                    sensor_payload["occupancy_callback"] = bridge_instance.occupancy_callbacks[device["id"]]

            sensors_data.append({
                "id": device["id"],
                "names": bridge_instance.device_names.get(device["id"], []),
                **sensor_payload
            })
    return sensors_data

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

    # Attempt to resolve ID via Matter bridge aliases first
    resolved_id = bridge_instance.resolve_id(device_id)

    # Check if target is a logical device via state aggregation
    logical_devices = logical_manager.get_all_devices().get("devices", [])
    logical_target = next((d for d in logical_devices if d["id"] == resolved_id), None)

    if logical_target:
        # Route execution payload to logical bridge
        node_id = logical_target["node_id"]
        endpoint_id = logical_target["endpoint_id"]
        client = logical_manager.registry.get(node_id)
        
        if client:
            try:
                if brightness_str is not None:
                    # Execute embedded Python script mapped to 'set_level'
                    client.execute_event(endpoint_id, "set_level", brightness_str)
                return {"status": "success", "id": resolved_id, "type": "logical"}
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Logical bridge execution failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Logical bridge client offline")

    # Proceed with physical Matter hardware actuation
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

@app.api_route("/api/script", methods=["GET", "POST"])
async def serve_script_api(request: Request):
    """Yields graphical HTML representation directly addressing arbitrary script deployment."""
    device_id = request.query_params.get("id", "")
    if not device_id:
        return HTMLResponse(content="Missing device ID parameter in URL (?id=...)", status_code=400)

    resolved_id = bridge_instance.resolve_id(device_id)

    if request.method == "GET":
        content = "#!/bin/bash\n\n# Add your script logic here\n"
        existing_script = bridge_instance.occupancy_callbacks.get(resolved_id)
        if existing_script and os.path.isfile(existing_script):
            try:
                with open(existing_script, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception:
                pass

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Edit Bash Script</title>
            <style>
                body {{ font-family: sans-serif; padding: 20px; }}
                textarea {{ width: 100%; max-width: 600px; padding: 10px; font-family: monospace; }}
                input[type="submit"] {{ padding: 10px 20px; cursor: pointer; }}
                .info {{ color: #555; margin-bottom: 15px; font-weight: bold; }}
            </style>
        </head>
        <body>
            <h2>Edit Callback Script</h2>
            <div class="info">Device ID: {resolved_id}</div>
            <form method="POST" action="/api/script?id={resolved_id}">
                <textarea name="content" rows="15" required>{content}</textarea><br><br>
                <input type="submit" value="Save and Register">
            </form>
        </body>
        </html>
        """
        return HTMLResponse(content=html)

    elif request.method == "POST":
        form_data = await request.form()
        content = form_data.get("content")

        if content is None:
            raise HTTPException(status_code=400, detail="Missing script content parameters")

        timestamp = int(time.time())
        script_dir = "./scripts"
        script_path = f"{script_dir}/callback_{resolved_id}_{timestamp}.sh"

        try:
            os.makedirs(script_dir, exist_ok=True)
            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(str(content).replace('\\r\\n', '\\n'))
            os.chmod(script_path, 0o755)
        except Exception as e:
            return HTMLResponse(content=f"Failed to save script: {e}", status_code=500)

        bridge_instance.occupancy_callbacks[resolved_id] = script_path
        bridge_instance._save_callbacks()

        success_html = f"""
        <!DOCTYPE html>
        <html>
        <head><title>Success</title></head>
        <body>
            <h2>Script saved and registered successfully.</h2>
            <p>Device: {resolved_id}</p>
            <p>Generated Path: {script_path}</p>
            <a href="/api/script?id={resolved_id}">Return to Editor</a>
        </body>
        </html>
        """
        return HTMLResponse(content=success_html)

def main():
    parser = argparse.ArgumentParser(description="Matter API Web Server")
    parser.add_argument("--port", type=int, default=8080, help="Web server port")
    args = parser.parse_args()
    
    app.state.port = args.port
    uvicorn.run(app, host="0.0.0.0", port=args.port)

if __name__ == "__main__":
    main()