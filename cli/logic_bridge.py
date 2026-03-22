import hashlib
import io
import json
import os
import sys
import urllib.request
from contextlib import redirect_stdout
from typing import Any, Dict, Optional


class LogicalBridgeClient:
    """HTTP client for a remote Matter Web Controller bridge."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.metadata_url = f"http://{host}:{port}/api/metadata"
        self.metadata: Dict[str, Any] = {}
        self.devices: Dict[str, Any] = {}

    def fetch_metadata(self) -> None:
        req = urllib.request.Request(self.metadata_url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.metadata = data
            self.devices = {dev["node_id"]: dev for dev in data.get("devices", [])}

    def execute_event(self, endpoint_id: str, event_name: str, *args) -> Optional[str]:
        """Run the embedded Python script for a device event."""
        device = self.devices.get(endpoint_id)
        if not device or event_name not in device.get("events", {}):
            raise ValueError("Target endpoint_id or event_name not found.")

        script = device["events"][event_name].get("script")

        original_argv = sys.argv[:]
        sys.argv = ["virtual_script"] + [str(a) for a in args]

        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                exec(script, {})
            return buf.getvalue().strip()
        finally:
            sys.argv = original_argv


class LogicalBridgeManager:
    """Registry of remote logical bridges with persistent cache."""

    def __init__(self, cache_file: str = "bridge_cache.json"):
        self.registry: Dict[str, LogicalBridgeClient] = {}
        self.cache_file = cache_file

    def load_cache(self) -> None:
        if not os.path.exists(self.cache_file):
            return
        with open(self.cache_file, "r") as f:
            for node_id, cfg in json.load(f).items():
                try:
                    self.add_bridge(cfg["ip"], cfg["port"], persist=False)
                except Exception:
                    pass  # skip offline bridges

    def _save_cache(self) -> None:
        data = {
            nid: {"ip": c.host, "port": c.port}
            for nid, c in self.registry.items()
        }
        with open(self.cache_file, "w") as f:
            json.dump(data, f)

    def add_bridge(self, ip: str, port: int, persist: bool = True) -> str:
        node_id = f"{ip}:{port}"
        client = LogicalBridgeClient(host=ip, port=port)
        client.fetch_metadata()
        self.registry[node_id] = client

        if persist:
            self._save_cache()

        return node_id

    def get_all_devices(self) -> Dict[str, Any]:
        """Aggregate and normalize device states from all bridges."""
        aggregated = []

        for node_id, client in self.registry.items():
            for endpoint_id, info in client.devices.items():
                # Read brightness level
                level = 0.0
                if "read_level" in info.get("events", {}):
                    try:
                        result = client.execute_event(endpoint_id, "read_level")
                        level = float(result) if result else 0.0
                    except Exception:
                        level = 0.0

                # Normalize to 0-254 range (auto-detect 0-1 vs 0-254 scale)
                if level > 1.0:
                    brightness_raw = int(max(0, min(254, level)))
                else:
                    brightness_raw = int(max(0, min(254, level * 254)))

                # Read color temperature
                color_temp = 0
                if "read_color_temperature" in info.get("events", {}):
                    try:
                        result = client.execute_event(endpoint_id, "read_color_temperature")
                        color_temp = int(float(result)) if result else 0
                    except Exception:
                        color_temp = 0

                # Build device record
                safe_id = "dev_" + hashlib.md5(f"{node_id}_{endpoint_id}".encode()).hexdigest()[:8]
                states = {"on_off": brightness_raw > 0, "brightness_raw": brightness_raw}
                if color_temp > 0:
                    states["color_temp_mireds"] = color_temp

                aggregated.append({
                    "id": safe_id,
                    "node_id": node_id,
                    "endpoint_id": endpoint_id,
                    "states": states,
                    "names": [info.get("name", endpoint_id)],
                })

        return {"total_devices": len(aggregated), "devices": aggregated}

    def refresh_bridges(self) -> int:
        count = 0
        for client in self.registry.values():
            try:
                client.fetch_metadata()
                count += 1
            except Exception:
                pass  # skip unreachable bridges
        return count
