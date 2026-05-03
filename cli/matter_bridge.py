import asyncio
import hashlib
import json
import logging
import os
import sys
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
    """Matter server process manager and device state cache."""

    def __init__(self, port):
        self.matter_port = port + 1
        self.server_url = f"ws://localhost:{self.matter_port}/ws"

        self.session = None
        self.client = None
        self.process = None
        self.listen_task = None

        self.cached_devices = []
        self.occupancy_history = {}
        self.device_names = {}
        self.occupancy_subscribers = {}

        # Load persisted caches
        self.cached_devices = self._load_json("devices_cache.txt", [])
        self.occupancy_history = self._load_json("occupancy_cache.json", {})
        self.device_names = self._load_json("names_cache.json", {})

    # -- JSON cache helpers --------------------------------------------------

    @staticmethod
    def _load_json(path: str, default):
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                logging.info(f"Loaded cache: {path}")
                return data
        except Exception as e:
            logging.error(f"Failed to load {path}: {e}")
            return default

    @staticmethod
    def _save_json(path: str, data):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logging.error(f"Failed to save {path}: {e}")

    def _save_names_cache(self):
        self._save_json("names_cache.json", self.device_names)

    # -- Process lifecycle ---------------------------------------------------

    async def start_process(self):
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "matter_server.server",
            "--storage-path", "./matter_storage",
            "--port", str(self.matter_port),
        )
        await asyncio.sleep(2.0)

    async def establish_connection(self) -> bool:
        self.session = ClientSession()
        self.client = MatterClient(self.server_url, self.session)

        for _ in range(30):
            try:
                await self.client.connect()
                return True
            except Exception:
                await asyncio.sleep(2.0)
        return False

    async def initialize(self, app, fabric_label: str | None = None):
        await self.start_process()
        if not await self.establish_connection():
            logging.error("Failed to connect to Matter server.")
            return

        init_ready = asyncio.Event()
        self.listen_task = asyncio.create_task(self.client.start_listening(init_ready))
        try:
            await asyncio.wait_for(init_ready.wait(), timeout=60)
        except asyncio.TimeoutError:
            logging.error("Matter client did not finish initial sync in time.")
            return

        self.client.subscribe_events(self._on_event, EventType.ATTRIBUTE_UPDATED)

        if fabric_label:
            try:
                await self.client.send_command("set_default_fabric_label", label=fabric_label)
                logging.info(f"Fabric label set to: {fabric_label}")
            except Exception as e:
                logging.warning(f"Failed to set fabric label: {e}")

        self._update_cache()
        logging.info("Matter bridge is fully operational.")

    async def shutdown(self, app):
        if self.listen_task:
            self.listen_task.cancel()
        if self.session:
            await self.session.close()
        if self.process:
            self.process.terminate()
            await self.process.wait()

    def is_ready(self) -> bool:
        return self.client is not None

    # -- Event handling & cache update ---------------------------------------

    def _on_event(self, event, data):
        self._update_cache()

    @staticmethod
    def _get_stable_id(node, ep_id) -> tuple[str, str | None]:
        """Generate a stable device ID from hardware UniqueID or SerialNumber.

        Returns (device_id, unique_id) where unique_id is the raw hardware
        identifier used, or None if falling back to node_id.
        """
        unique_id = None
        # Basic Information cluster (40) is on endpoint 0
        if 0 in node.endpoints and 40 in node.endpoints[0].clusters:
            unique_id = node.get_attribute_value(0, 40, 18)  # UniqueID
            if unique_id is None:
                unique_id = node.get_attribute_value(0, 40, 15)  # SerialNumber

        raw = f"{unique_id}_{ep_id}" if unique_id else f"{node.node_id}_{ep_id}"
        device_id = f"dev_{hashlib.md5(raw.encode()).hexdigest()[:8]}"
        return device_id, unique_id

    def _migrate_ids(self, devices: list[dict]):
        """Migrate cache keys from old dev_{node}_{ep} format to new stable IDs."""
        mapping = {}
        for dev in devices:
            old_id = f"dev_{dev['node_id']}_{dev['endpoint_id']}"
            if old_id != dev["id"]:
                mapping[old_id] = dev["id"]

        if not mapping:
            return

        # Migrate device_names
        for old_id, new_id in mapping.items():
            if old_id in self.device_names:
                existing = self.device_names.pop(old_id)
                self.device_names.setdefault(new_id, [])
                for name in existing:
                    if name not in self.device_names[new_id]:
                        self.device_names[new_id].append(name)
        self._save_names_cache()

        # Migrate occupancy_history
        for old_id, new_id in mapping.items():
            if old_id in self.occupancy_history:
                self.occupancy_history[new_id] = self.occupancy_history.pop(old_id)
        self._save_json("occupancy_cache.json", self.occupancy_history)

        # Migrate occupancy_subscribers (in-memory)
        for old_id, new_id in mapping.items():
            if old_id in self.occupancy_subscribers:
                self.occupancy_subscribers[new_id] = self.occupancy_subscribers.pop(old_id)

        logging.info(f"Migrated {len(mapping)} device IDs to stable format.")

    def _update_cache(self):
        """Extract states from all Matter nodes and update local caches."""
        if not self.client:
            return

        devices = []
        occupancy_updated = False

        for node in self.client.get_nodes():
            for ep_id, endpoint in node.endpoints.items():
                device_id, unique_id = self._get_stable_id(node, ep_id)
                states = {}

                # On/Off cluster
                if 6 in endpoint.clusters:
                    raw = node.get_attribute_value(ep_id, 6, 0)
                    states["on_off"] = bool(raw) if raw is not None else None

                # Level control cluster
                if 8 in endpoint.clusters:
                    states["brightness_raw"] = node.get_attribute_value(ep_id, 8, 0)

                # Color control cluster
                if 768 in endpoint.clusters:
                    states["color_temp_mireds"] = node.get_attribute_value(ep_id, 768, 7)

                # Sensor clusters
                for cluster_id, (name, attr_id, _) in SENSOR_CLUSTERS.items():
                    if cluster_id in endpoint.clusters:
                        val = node.get_attribute_value(ep_id, cluster_id, attr_id)
                        if val is not None:
                            states[name] = int(val)

                            if name == "occupancy":
                                cur = int(val)
                                prev_dev = next((d for d in self.cached_devices if d["id"] == device_id), None)
                                prev = prev_dev.get("states", {}).get("occupancy", 0) if prev_dev else 0

                                if cur != prev and device_id in self.occupancy_subscribers:
                                    ts = int(time.time())
                                    for q in self.occupancy_subscribers[device_id]:
                                        q.put_nowait((cur, ts))

                                if cur == 1 and prev == 0:
                                    self.occupancy_history[device_id] = int(time.time())
                                    occupancy_updated = True

                devices.append({
                    "id": device_id,
                    "node_id": node.node_id,
                    "endpoint_id": ep_id,
                    "unique_id": unique_id,
                    "states": states,
                })

        self._migrate_ids(devices)
        self.cached_devices = devices
        self._save_json("devices_cache.txt", devices)

        if occupancy_updated:
            self._save_json("occupancy_cache.json", self.occupancy_history)
