import argparse
import asyncio
import logging
from aiohttp import web, ClientSession
from matter_server.client.client import MatterClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

global_session = None
global_client = None
global_matter_process = None
global_listen_task = None

MATTER_PORT = 5580
MATTER_SERVER_URL = ""

async def init_servers(app):
    global global_session, global_client, global_matter_process, global_listen_task
    
    logging.info(f"1. Launching internal Matter Server subprocess on port {MATTER_PORT}...")
    global_matter_process = await asyncio.create_subprocess_exec(
        "python3", "-m", "matter_server.server", "--storage-path", "./matter_storage", "--port", str(MATTER_PORT),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    
    await asyncio.sleep(4.0)
    
    logging.info("2. Establishing client connection (ClientSession)...")
    global_session = ClientSession()
    global_client = MatterClient(MATTER_SERVER_URL, global_session)
    
    logging.info("3. Activating background listening task...")
    global_listen_task = asyncio.create_task(global_client.start_listening())
    
    await asyncio.sleep(2.0)
    logging.info("4. DONE: Web server is ready to accept HTTP requests!")

async def close_servers(app):
    logging.info("Executing resource cleanup procedures...")
    if global_listen_task:
        global_listen_task.cancel()
    if global_session:
        await global_session.close()
    if global_matter_process:
        logging.info("Terminating internal Matter Server subprocess...")
        global_matter_process.terminate()
        await global_matter_process.wait()

async def serve_lighting_api(request):
    logging.info("--- Received HTTP GET request at /api/lights ---")
    if not global_client:
         return web.json_response({"error": "Server not ready"}, status=503)

    nodes = global_client.get_nodes()
    logging.info(f"DONE: Retrieved data for {len(nodes)} network nodes.")

    lighting_devices = []
    for node in nodes:
        for endpoint_id, endpoint in node.endpoints.items():
            if 6 in endpoint.clusters:
                power_state = node.get_attribute_value(endpoint_id, 6, 0)
                brightness_level = node.get_attribute_value(endpoint_id, 8, 0) if 8 in endpoint.clusters else None
                
                lighting_devices.append({
                    "id": f"Node {node.node_id} - EP {endpoint_id}",
                    "state": power_state,
                    "brightness": brightness_level
                })
    
    return web.json_response(lighting_devices)

def main():
    parser = argparse.ArgumentParser(description="Matter API Web Server")
    parser.add_argument("--port", type=int, default=8080, help="Web server port (Matter port will be port + 1)")
    args = parser.parse_args()

    web_port = args.port
    global MATTER_PORT, MATTER_SERVER_URL
    MATTER_PORT = web_port + 1
    MATTER_SERVER_URL = f"ws://localhost:{MATTER_PORT}/ws"

    app = web.Application()
    app.on_startup.append(init_servers)
    app.on_cleanup.append(close_servers)
    app.router.add_get('/api/lights', serve_lighting_api)
    
    logging.info(f"Bootstrapping Web server on port {web_port}, Matter server on port {MATTER_PORT}...")
    web.run_app(app, host='0.0.0.0', port=web_port)

if __name__ == '__main__':
    main()
