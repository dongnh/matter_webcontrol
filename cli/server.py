import argparse
import asyncio
import logging
from aiohttp import web, ClientSession
from matter_server.client.client import MatterClient
import chip.clusters.Objects as Clusters

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
            logging.info("DONE: Web server is ready.")
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

def require_server_ready(handler):
    """Decorator to ensure the server is ready before processing API requests."""
    async def wrapper(request):
        bridge = request.app['bridge']
        if not bridge.is_ready():
            return web.json_response({"error": "Server not ready"}, status=503)
        return await handler(request, bridge)
    return wrapper

@require_server_ready
async def serve_lighting_api(request, bridge):
    logging.info("--- Received HTTP GET request at /api/lights ---")
    lighting_devices = []
    
    for node in bridge.client.get_nodes():
        for endpoint_id, endpoint in node.endpoints.items():
            if 6 in endpoint.clusters: # On/Off cluster
                # 1. Trích xuất trạng thái (State)
                raw_state = node.get_attribute_value(endpoint_id, 6, 0)
                state = bool(raw_state) if raw_state is not None else None

                # 2. Trích xuất và chuẩn hóa độ sáng (Brightness)
                mapped_brightness = None
                if 8 in endpoint.clusters:
                    raw_brightness = node.get_attribute_value(endpoint_id, 8, 0)
                    if raw_brightness is not None:
                        # Chuẩn Matter quy định CurrentLevel tối đa là 254
                        mapped_brightness = round(max(0.0, min(1.0, raw_brightness / 254.0)), 2)
                        
                        # Ép độ sáng về 0.0 nếu thiết bị đang tắt để đồng bộ trạng thái
                        if not state:
                            mapped_brightness = 0.0

                # 3. Trích xuất và chuyển đổi nhiệt độ màu (Color Temperature)
                color_temp_kelvin = None
                if 768 in endpoint.clusters:
                    color_temp_mireds = node.get_attribute_value(endpoint_id, 768, 7)
                    # Kiểm tra tồn tại và lớn hơn 0 để tránh ZeroDivisionError
                    if color_temp_mireds is not None and color_temp_mireds > 0:
                        color_temp_kelvin = int(1000000 / color_temp_mireds)
                
                lighting_devices.append({
                    "id": f"Node {node.node_id} - EP {endpoint_id}",
                    "brightness": mapped_brightness,
                    "temperature": color_temp_kelvin
                })
                
    return web.json_response(lighting_devices)

@require_server_ready
async def serve_sensors_api(request, bridge):
    logging.info("--- Received HTTP GET request at /api/sensors ---")
    sensors_data = []

    for node in bridge.client.get_nodes():
        for endpoint_id, endpoint in node.endpoints.items():
            for cluster_id, (sensor_name, attr_id, _) in SENSOR_CLUSTERS.items():
                if cluster_id in endpoint.clusters:
                    val = node.get_attribute_value(endpoint_id, cluster_id, attr_id)
                    if val is not None:
                        sensors_data.append({
                            "id": f"Node {node.node_id} - EP {endpoint_id} - {sensor_name}",
                            "name": sensor_name,
                            "value": int(val)
                        })
    return web.json_response(sensors_data)

def extract_matter_pin(setup_code):
    # Remove formatting characters
    clean_code = setup_code.replace("-", "").replace(" ", "")
    
    # Validate payload length based on Matter Core Specification
    if len(clean_code) not in (11, 21) or not clean_code.isdigit():
        raise ValueError("Invalid manual pairing code format")
    
    # Isolate segment 2 and segment 3
    value_2 = int(clean_code[1:6])
    value_3 = int(clean_code[6:10])
    
    # Bitwise reconstruction of the 27-bit PIN code
    pin_code = (value_3 << 14) | (value_2 & 0x3FFF)
    
    return pin_code

@require_server_ready
async def serve_commission_api(request, bridge):
    logging.info("--- Received HTTP GET request at /api/register ---")
    setup_code = request.query.get('code')
    ip_address = request.query.get('ip')

    if not setup_code:
        return web.json_response({"error": "Missing setup code"}, status=400)

    try:
        if ip_address:
            logging.info(f"Executing directed commissioning over IP: {ip_address}")
            
            # Extract PIN internally without external dependencies
            pin_code = extract_matter_pin(setup_code)

            await bridge.client.send_command(
                "commission_on_network", 
                setup_pin_code=pin_code, 
                ip_address=ip_address
            )
        else:
            logging.info("Executing auto-discovery commissioning")
            await bridge.client.send_command(
                "commission_with_code", 
                code=setup_code
            )
            
        return web.json_response({"status": "success", "code": setup_code, "ip": ip_address})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
    
@require_server_ready
async def serve_set_api(request, bridge):
    # Support both GET and POST requests
    data = await request.json() if request.method == 'POST' else request.query

    device_id = data.get('id')
    if not device_id:
        return web.json_response({"error": "Missing device id"}, status=400)

    # Parse device identifier
    try:
        parts = device_id.replace("Node ", "").split(" - EP ")
        node_id = int(parts[0])
        endpoint_id = int(parts[1])
    except Exception:
        return web.json_response({"error": "Invalid ID format. Expected 'Node X - EP Y'"}, status=400)

    brightness_str = data.get('brightness')
    temperature_str = data.get('temperature')

    try:
        # 1. Handle Brightness and implied On/Off state
        if brightness_str is not None:
            brightness = float(brightness_str)
            brightness = max(0.0, min(1.0, brightness))
            
            if brightness == 0.0:
                # Transmit Off command if brightness is exactly 0
                cmd = Clusters.OnOff.Commands.Off()
                await bridge.client.send_device_command(node_id, endpoint_id, cmd)
            else:
                # Transmit MoveToLevelWithOnOff for values > 0
                level = max(1, int(brightness * 254))
                cmd = Clusters.LevelControl.Commands.MoveToLevelWithOnOff(level=level, transitionTime=0)
                await bridge.client.send_device_command(node_id, endpoint_id, cmd)

        # 2. Handle Color Temperature in Kelvin
        if temperature_str is not None:
            temp_kelvin = int(temperature_str)
            if temp_kelvin > 0:
                # Calculate inverse for Mireds scale
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

def main():
    parser = argparse.ArgumentParser(description="Matter API Web Server")
    parser.add_argument("--port", type=int, default=8080, help="Web server port")
    args = parser.parse_args()

    app = web.Application()
    
    bridge = MatterBridgeServer(args.port)
    app['bridge'] = bridge
    
    app.on_startup.append(bridge.initialize)
    app.on_cleanup.append(bridge.shutdown)
    
    app.router.add_get('/api/lights', serve_lighting_api)
    app.router.add_get('/api/sensors', serve_sensors_api)
    app.router.add_get('/api/register', serve_commission_api)
    app.router.add_get('/api/set', serve_set_api)
    
    logging.info(f"Bootstrapping Web server on port {args.port}...")
    web.run_app(app, host='0.0.0.0', port=args.port)

if __name__ == '__main__':
    main()