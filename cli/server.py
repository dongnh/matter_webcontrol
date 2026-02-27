import argparse
import asyncio
import logging
import json
import os
import time
from aiohttp import web, ClientSession
from matter_server.client.client import MatterClient
from matter_server.common.models import EventType
import chip.clusters.Objects as Clusters
import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

SENSOR_CLUSTERS = {
    1024: ("illuminance", 0, 1),
    1026: ("temperature", 0, 100),
    1027: ("pressure", 0, 10),
    1029: ("humidity", 0, 100),
    1030: ("occupancy", 0, 1),
    69: ("contact", 0, 1),
}

class MatterBridgeServer:
    """Encapsulates the Matter server process and client connection state."""
    
    def __init__(self, port):
        self.matter_port = port + 1
        self.server_url = f"ws://localhost:{self.matter_port}/ws"
        
        self.session = None
        self.client = None
        self.process = None
        self.listen_task = None
        
        self.cache_file = "devices_cache.txt"
        self.cached_devices = []
        self._load_device_cache()
        
        
        # Occupancy history storage
        self.occupancy_cache_file = "occupancy_cache.json"
        self.occupancy_history = {}
        self._load_occupancy_history()

    def _load_device_cache(self):
        """Loads device states from the local file during initialization."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as file:
                    self.cached_devices = json.load(file)
                logging.info(f"Successfully loaded {len(self.cached_devices)} devices from cache.")
            except Exception as e:
                logging.error(f"Failed to load device cache: {e}")

    def _load_occupancy_history(self):
        """Loads occupancy timestamps from cache file."""
        if os.path.exists(self.occupancy_cache_file):
            try:
                with open(self.occupancy_cache_file, "r", encoding="utf-8") as file:
                    self.occupancy_history = json.load(file)
            except Exception as e:
                logging.error(f"Failed to load occupancy cache: {e}")

    def _save_occupancy_history(self):
        """Persists occupancy timestamps to cache file."""
        try:
            with open(self.occupancy_cache_file, "w", encoding="utf-8") as file:
                json.dump(self.occupancy_history, file, indent=4)
        except Exception as e:
            logging.error(f"Failed to save occupancy cache: {e}")

    async def start_process(self):
        """Launches the internal Matter Server subprocess."""
        logging.info(f"Launching internal Matter Server subprocess on port {self.matter_port}...")
        self.process = await asyncio.create_subprocess_exec(
            "python3", "-m", "matter_server.server", "--storage-path", "./matter_storage", "--port", str(self.matter_port),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await asyncio.sleep(2.0)

    async def establish_connection(self):
        """Attempts to connect to the Matter server with a polling mechanism."""
        logging.info("Establishing client connection...")
        self.session = ClientSession()
        self.client = MatterClient(self.server_url, self.session)
        
        for attempt in range(15):
            try:
                await self.client.connect()
                logging.info("Connection established successfully.")
                return True
            except Exception:
                logging.info(f"Awaiting server readiness (attempt {attempt + 1}/15)...")
                await asyncio.sleep(2.0)
        return False

    async def initialize(self, app):
        """Bootstrap routine for server startup."""
        await self.start_process()
        is_connected = await self.establish_connection()
        
        if is_connected:
            self.listen_task = asyncio.create_task(self.client.start_listening())
            
            # Subscribe to attribute updates to eliminate polling
            self.client.subscribe_events(self._on_event, EventType.ATTRIBUTE_UPDATED)
            
            # Perform initial cache population
            self._update_cache()
            
            logging.info("DONE: Web server is ready and subscribed to events.")
        else:
            logging.error("Failed to verify Matter server connection.")

    async def shutdown(self, app):
        """Cleanup routine for graceful shutdown."""
        logging.info("Executing resource cleanup procedures...")
        if self.listen_task:
            self.listen_task.cancel()
        if self.session:
            await self.session.close()
        if self.process:
            self.process.terminate()
            await self.process.wait()

    def is_ready(self):
        """Verifies if the client is operational."""
        return self.client is not None

    def _on_event(self, event, data):
        """Callback triggered immediately on Matter device state changes."""
        self._update_cache()

    def _update_cache(self):
        """Extracts states from all connected nodes and updates the local cache files."""
        devices = []
        if not self.client:
            return

        occupancy_updated = False

        for node in self.client.get_nodes():
            for endpoint_id, endpoint in node.endpoints.items():
                device_id = f"dev_{node.node_id}_{endpoint_id}"
                states = {}

                if 6 in endpoint.clusters:
                    raw_val = node.get_attribute_value(endpoint_id, 6, 0)
                    states["on_off"] = bool(raw_val) if raw_val is not None else None
                if 8 in endpoint.clusters:
                    states["brightness_raw"] = node.get_attribute_value(endpoint_id, 8, 0)
                if 768 in endpoint.clusters:
                    states["color_temp_mireds"] = node.get_attribute_value(endpoint_id, 768, 7)

                for cluster_id, (sensor_name, attr_id, _) in SENSOR_CLUSTERS.items():
                    if cluster_id in endpoint.clusters:
                        val = node.get_attribute_value(endpoint_id, cluster_id, attr_id)
                        if val is not None:
                            states[sensor_name] = int(val)
                            
                            if sensor_name == "occupancy" and int(val) == 1:
                                self.occupancy_history[device_id] = int(time.time())
                                occupancy_updated = True

                devices.append({
                    "id": device_id,
                    "node_id": node.node_id,
                    "endpoint_id": endpoint_id,
                    "states": states
                })

        self.cached_devices = devices
        
        try:
            with open(self.cache_file, "w", encoding="utf-8") as file:
                json.dump(devices, file, indent=4)
        except Exception as e:
            logging.error(f"Failed to persist device cache: {e}")

        if occupancy_updated:
            self._save_occupancy_history()

def require_server_ready(handler):
    """Decorator to ensure the server is ready before processing API requests."""
    async def wrapper(request):
        bridge = request.app['bridge']
        if not bridge.is_ready():
            return web.json_response({"error": "Server not ready"}, status=503)
        return await handler(request, bridge)
    return wrapper

@require_server_ready
async def serve_all_devices_api(request, bridge):
    """API Endpoint to retrieve all cached devices and their absolute states."""
    if hasattr(bridge, 'cached_devices') and bridge.cached_devices:
        return web.json_response(bridge.cached_devices)
    return web.json_response({"error": "Cache is empty or uninitialized"}, status=503)

@require_server_ready
async def serve_lighting_api(request, bridge):
    """Retrieves lighting states from the cache memory."""
    lighting_devices = []
    
    for device in bridge.cached_devices:
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
                "state": state,
                "brightness": mapped_brightness,
                "temperature": color_temp_kelvin
            })
                
    return web.json_response(lighting_devices)

@require_server_ready
async def serve_sensors_api(request, bridge):
    """Retrieves aggregated sensor states and formats occupancy history."""
    sensors_data = []
    sensor_keys = ["illuminance", "temperature", "pressure", "humidity", "occupancy", "contact"]

    for device in bridge.cached_devices:
        states = device.get("states", {})
        sensor_payload = {}
        
        for key in sensor_keys:
            if key in states:
                sensor_payload[key] = states[key]
                
        if sensor_payload:
            if "occupancy" in sensor_payload:
                last_active = bridge.occupancy_history.get(device["id"])
                if last_active:
                    # Convert Unix epoch integer to human-readable string
                    readable_time = datetime.datetime.fromtimestamp(last_active).strftime('%Y-%m-%d %H:%M:%S')
                    sensor_payload["occupancy_last_active"] = readable_time

            sensors_data.append({
                "id": device["id"],
                **sensor_payload
            })

    return web.json_response(sensors_data)

def extract_matter_pin(setup_code):
    clean_code = setup_code.replace("-", "").replace(" ", "")
    
    if len(clean_code) not in (11, 21) or not clean_code.isdigit():
        raise ValueError("Invalid manual pairing code format")
    
    value_2 = int(clean_code[1:6])
    value_3 = int(clean_code[6:10])
    
    pin_code = (value_3 << 14) | (value_2 & 0x3FFF)
    
    return pin_code

@require_server_ready
async def serve_commission_api(request, bridge):
    setup_code = request.query.get('code')
    ip_address = request.query.get('ip')

    if not setup_code:
        return web.json_response({"error": "Missing setup code"}, status=400)

    try:
        if ip_address:
            pin_code = extract_matter_pin(setup_code)
            await bridge.client.send_command(
                "commission_on_network", 
                setup_pin_code=pin_code, 
                ip_address=ip_address
            )
        else:
            await bridge.client.send_command(
                "commission_with_code", 
                code=setup_code
            )
            
        return web.json_response({"status": "success", "code": setup_code, "ip": ip_address})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    
@require_server_ready
async def serve_set_api(request, bridge):
    data = await request.json() if request.method == 'POST' else request.query

    device_id = data.get('id')
    if not device_id:
        return web.json_response({"error": "Missing device id"}, status=400)

    try:
        parts = device_id.replace("dev_", "").split("_")
        node_id = int(parts[0])
        endpoint_id = int(parts[1])
    except Exception:
        return web.json_response({"error": "Invalid ID format. Expected 'dev_X_Y'"}, status=400)

    brightness_str = data.get('brightness')
    temperature_str = data.get('temperature')

    try:
        if brightness_str is not None:
            brightness = float(brightness_str)
            brightness = max(0.0, min(1.0, brightness))
            
            if brightness == 0.0:
                cmd = Clusters.OnOff.Commands.Off()
                await bridge.client.send_device_command(node_id, endpoint_id, cmd)
            else:
                level = max(1, int(brightness * 254))
                cmd = Clusters.LevelControl.Commands.MoveToLevelWithOnOff(level=level, transitionTime=0)
                await bridge.client.send_device_command(node_id, endpoint_id, cmd)

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
                await bridge.client.send_device_command(node_id, endpoint_id, cmd)

        return web.json_response({"status": "success", "id": device_id})

    except Exception as e:
        logging.error(f"Command execution failed: {e}")
        return web.json_response({"error": str(e)}, status=500)
    
@require_server_ready
async def serve_single_sensor_api(request, bridge):
    """Retrieves state and formatted occupancy history for a specific sensor ID."""
    device_id = request.query.get('id')
    if not device_id:
        return web.json_response({"error": "Missing sensor id parameter"}, status=400)

    sensor_keys = ["illuminance", "temperature", "pressure", "humidity", "occupancy", "contact"]

    for device in bridge.cached_devices:
        if device.get("id") == device_id:
            states = device.get("states", {})
            sensor_payload = {}
            
            for key in sensor_keys:
                if key in states:
                    sensor_payload[key] = states[key]
                    
            if sensor_payload:
                if "occupancy" in sensor_payload:
                    last_active = bridge.occupancy_history.get(device_id)
                    if last_active:
                        # Convert Unix epoch integer to human-readable string
                        readable_time = datetime.datetime.fromtimestamp(last_active).strftime('%Y-%m-%d %H:%M:%S')
                        sensor_payload["occupancy_last_active"] = readable_time

                return web.json_response({
                    "id": device_id,
                    **sensor_payload
                })
            
            return web.json_response({"error": "Device exists but contains no sensor clusters"}, status=404)

    return web.json_response({"error": "Sensor not found in cache"}, status=404)    

def main():
    parser = argparse.ArgumentParser(description="Matter API Web Server")
    parser.add_argument("--port", type=int, default=8080, help="Web server port")
    args = parser.parse_args()

    app = web.Application()
    
    bridge = MatterBridgeServer(args.port)
    app['bridge'] = bridge
    
    app.on_startup.append(bridge.initialize)
    app.on_cleanup.append(bridge.shutdown)
    
    app.router.add_get('/api/devices', serve_all_devices_api)
    app.router.add_get('/api/lights', serve_lighting_api)
    app.router.add_get('/api/sensors', serve_sensors_api)
    app.router.add_get('/api/register', serve_commission_api)
    app.router.add_get('/api/set', serve_set_api)
    app.router.add_post('/api/set', serve_set_api)
    app.router.add_get('/api/sensor', serve_single_sensor_api)
    
    logging.info(f"Bootstrapping Web server on port {args.port}...")
    web.run_app(app, host='0.0.0.0', port=args.port)

if __name__ == '__main__':
    main()