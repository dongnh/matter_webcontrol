import json
import os
import sys
import io
import urllib.request
from contextlib import redirect_stdout, asynccontextmanager
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException
import uvicorn
import hashlib
from typing import Dict, Any

class LogicalBridgeClient:
    def __init__(self, host: str, port: int):
        # Initialize connection parameters
        self.metadata_url = f"http://{host}:{port}/api/metadata"
        self.metadata: Dict[str, Any] = {}
        self.devices: Dict[str, Any] = {}

    def fetch_metadata(self) -> None:
        # Retrieve JSON metadata schema
        req = urllib.request.Request(self.metadata_url)
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            self.metadata = data
            self.devices = {dev["node_id"]: dev for dev in data.get("devices", [])}

    def execute_event(self, endpoint_id: str, event_name: str, *args) -> Optional[str]:
        # Execute embedded Python script for the hardware event
        device = self.devices.get(endpoint_id)
        if not device or event_name not in device.get("events", {}):
            raise ValueError("Target endpoint_id or event_name not found.")

        script = device["events"][event_name].get("script")
        
        # Override sys.argv safely
        original_argv = sys.argv[:]
        sys.argv = ["virtual_script"] + [str(arg) for arg in args]

        output_buffer = io.StringIO()
        try:
            with redirect_stdout(output_buffer):
                exec(script, {})
            return output_buffer.getvalue().strip()
        finally:
            sys.argv = original_argv

class LogicalBridgeManager:
    def __init__(self, cache_file: str = "bridge_cache.json"):
        # Initialize registry and cache file path
        self.registry: Dict[str, LogicalBridgeClient] = {}
        self.cache_file = cache_file

    def load_cache(self) -> None:
        # Load bridges from persistent storage during startup
        if not os.path.exists(self.cache_file):
            return
        with open(self.cache_file, "r") as f:
            data = json.load(f)
            for node_id, config in data.items():
                try:
                    self.add_bridge(config["ip"], config["port"], save_to_cache=False)
                except Exception:
                    # Ignore offline bridges during initialization
                    pass

    def _save_cache(self) -> None:
        # Persist current registry state to local storage
        data = {
            node_id: {"ip": client.metadata_url.split("//")[1].split(":")[0], 
                      "port": int(client.metadata_url.split(":")[2].split("/")[0])}
            for node_id, client in self.registry.items()
        }
        with open(self.cache_file, "w") as f:
            json.dump(data, f)

    def add_bridge(self, ip: str, port: int, save_to_cache: bool = True) -> str:
        # Register a new logical bridge and optionally persist it
        node_id = f"{ip}:{port}"
        client = LogicalBridgeClient(host=ip, port=port)
        client.fetch_metadata()
        self.registry[node_id] = client
        
        if save_to_cache:
            self._save_cache()
            
        return node_id

    def get_all_devices(self) -> Dict[str, Any]:
        # Aggregate core identifiers and current states from all registered bridges
        aggregated = []
        for node_id, client in self.registry.items():
            for endpoint_id, device_info in client.devices.items():
                
                # Fetch logical level with error handling
                logical_level = 0.0
                if "read_level" in device_info.get("events", {}):
                    try:
                        result = client.execute_event(endpoint_id, "read_level")
                        logical_level = float(result) if result else 0.0
                    except Exception:
                        logical_level = 0.0

                # State computation for dimmable devices
                is_on = logical_level > 0.0
                brightness_raw = int(logical_level * 254)
                
                # Construct safe unique ID
                raw_id = f"{node_id}_{endpoint_id}"
                safe_id = "dev_" + hashlib.md5(raw_id.encode()).hexdigest()[:8]

                # Extract explicit name from metadata, fallback to endpoint_id
                device_name = device_info.get("name", endpoint_id)

                # Construct record matching the hardware constraints
                record = {
                    "id": safe_id,
                    "node_id": node_id,
                    "endpoint_id": endpoint_id,
                    "states": {
                        "on_off": is_on,
                        "brightness_raw": brightness_raw
                    },
                    "names": [device_name]
                }
                aggregated.append(record)
                
        return {"total_devices": len(aggregated), "devices": aggregated}

# Instantiate the global manager
manager = LogicalBridgeManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Execute automated state restoration upon server initialization
    manager.load_cache()
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/api/add")
def api_add_bridge(ip: str, port: int) -> Dict[str, str]:
    try:
        node_id = manager.add_bridge(ip, port)
        return {"status": "success", "message": f"Registered {node_id}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/devices")
def api_get_all_devices() -> Dict[str, Any]:
    return manager.get_all_devices()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)