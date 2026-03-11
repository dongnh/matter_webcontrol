import asyncio
import logging
import json
import os
import time
from aiohttp import ClientSession
from matter_server.client.client import MatterClient
from matter_server.common.models import EventType

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
        
        # Initialize and hydrate device cache
        self.cache_file = "devices_cache.txt"
        self.cached_devices = []
        self._load_device_cache()
        
        # Initialize and hydrate occupancy history
        self.occupancy_cache_file = "occupancy_cache.json"
        self.occupancy_history = {}
        self._load_occupancy_history()
        
        # Initialize and hydrate names/aliases
        self.names_cache_file = "names_cache.json"
        self.device_names = {}
        self._load_names_cache()

        # Initialize subscribers for Server-Sent Events
        self.occupancy_subscribers = {}

    def _load_device_cache(self):
        """Loads device states from the local file during initialization."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as file:
                    self.cached_devices = json.load(file)
                logging.info(f"Loaded {len(self.cached_devices)} devices from cache.")
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

    def _load_names_cache(self):
        """Loads device aliases from cache file."""
        if os.path.exists(self.names_cache_file):
            try:
                with open(self.names_cache_file, "r", encoding="utf-8") as file:
                    self.device_names = json.load(file)
            except Exception as e:
                logging.error(f"Failed to load names cache: {e}")

    def _save_names_cache(self):
        """Persists device aliases to cache file."""
        try:
            with open(self.names_cache_file, "w", encoding="utf-8") as file:
                json.dump(self.device_names, file, indent=4)
        except Exception as e:
            logging.error(f"Failed to save names cache: {e}")

    def resolve_id(self, identifier):
        """Resolves a given name or ID to the standardized device ID."""
        if not identifier:
            return None
        if identifier.startswith("dev_"):
            return identifier
        for dev_id, names in self.device_names.items():
            if identifier in names:
                return dev_id
        return identifier

    async def start_process(self):
        """Launches the internal Matter Server subprocess and streams logs directly to the console."""
        self.process = await asyncio.create_subprocess_exec(
            "python3", "-m", "matter_server.server", "--storage-path", "./matter_storage", "--port", str(self.matter_port)
        )
        await asyncio.sleep(2.0)

    async def establish_connection(self):
        """Attempts to connect to the Matter server."""
        self.session = ClientSession()
        self.client = MatterClient(self.server_url, self.session)
        
        for attempt in range(30):
            try:
                await self.client.connect()
                return True
            except Exception:
                await asyncio.sleep(2.0)
        return False

    async def initialize(self, app):
        """Bootstrap routine for server startup."""
        await self.start_process()
        is_connected = await self.establish_connection()
        
        if is_connected:
            self.listen_task = asyncio.create_task(self.client.start_listening())
            self.client.subscribe_events(self._on_event, EventType.ATTRIBUTE_UPDATED)
            self._update_cache()
            logging.info("Matter bridge is fully operational.")
        else:
            logging.error("Failed to verify Matter server connection.")

    async def shutdown(self, app):
        """Cleanup routine for graceful shutdown."""
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
                            
                            if sensor_name == "occupancy":
                                current_occupancy = int(val)
                                prev_device = next((d for d in self.cached_devices if d["id"] == device_id), None)
                                prev_occupancy = prev_device.get("states", {}).get("occupancy", 0) if prev_device else 0
                                
                                if current_occupancy != prev_occupancy:
                                    event_timestamp = int(time.time())
                                    if device_id in self.occupancy_subscribers:
                                        for q in self.occupancy_subscribers[device_id]:
                                            # Queue both state and timestamp
                                            q.put_nowait((current_occupancy, event_timestamp))
                                
                                if current_occupancy == 1 and prev_occupancy == 0:
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