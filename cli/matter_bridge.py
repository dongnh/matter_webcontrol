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

from cli import paths

SENSOR_CLUSTERS = {
    1024: ("illuminance", 0, 1),
    1026: ("temperature", 0, 100),
    1027: ("pressure", 0, 10),
    1029: ("humidity", 0, 100),
    1030: ("occupancy", 0, 1),
    69: ("contact", 0, 1),
}

# Bumped when the device_id derivation changes; gates the one-shot ID migration.
SCHEMA_VERSION = "stable_md5_v1"
# Coalesce devices_cache disk writes to at most one per this many seconds.
FLUSH_INTERVAL = 3.0
# Bound each SSE subscriber queue so a slow consumer can't grow it unbounded.
OCCUPANCY_QUEUE_MAXSIZE = 100


class MatterBridgeServer:
    """Matter server process manager and device state cache."""

    def __init__(self, port):
        self.matter_port = port + 1
        self.server_url = f"ws://localhost:{self.matter_port}/ws"

        self.session = None
        self.client = None
        self.process = None
        self.listen_task = None

        # Debounced devices_cache persistence state.
        self._flush_handle = None
        self._last_saved_devices: str | None = None

        self.cached_devices = []
        self.occupancy_history = {}
        self.device_names = {}
        self.occupancy_subscribers = {}

        # Load persisted caches
        self.cached_devices = self._load_json(paths.devices_cache(), [])
        self.occupancy_history = self._load_json(paths.occupancy_cache(), {})
        self.device_names = self._load_json(paths.names_cache(), {})

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
        """Atomically write JSON (tmp + os.replace). Re-raises OSError after
        logging so callers can no longer report success on a failed write."""
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            os.replace(tmp, path)
        except OSError as e:
            logging.error("Failed to save %s: %s", path, e)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise

    def _save_names_cache(self):
        self._save_json(paths.names_cache(), self.device_names)

    # -- Debounced devices_cache persistence ---------------------------------

    def _schedule_flush(self) -> None:
        """Request a coalesced devices_cache write.

        Called from the per-event hot path: schedules one write at most every
        FLUSH_INTERVAL seconds instead of writing on every ATTRIBUTE_UPDATED.
        Falls back to an inline write when there is no running event loop
        (e.g. a manual refresh on a worker thread, or tests).
        """
        if self._flush_handle is not None:
            return  # a flush is already pending; it will pick up the latest state
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._flush_devices_cache()
            return
        self._flush_handle = loop.call_later(FLUSH_INTERVAL, self._flush_devices_cache)

    def _flush_devices_cache(self) -> None:
        """Write devices_cache only if the serialized snapshot actually changed.

        Best-effort: devices_cache is rebuilt from Matter on restart, so a write
        failure (already logged by _save_json) is swallowed rather than crashing
        the fire-and-forget callback."""
        self._flush_handle = None
        snapshot = json.dumps(self.cached_devices, sort_keys=True, default=str)
        if snapshot == self._last_saved_devices:
            return
        try:
            self._save_json(paths.devices_cache(), self.cached_devices)
            self._last_saved_devices = snapshot
        except OSError:
            pass  # logged in _save_json; cache repopulates from Matter on restart

    # -- Process lifecycle ---------------------------------------------------

    async def start_process(self):
        storage_path = paths.matter_storage()
        logging.info("Matter fabric storage path: %s", storage_path)
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "matter_server.server",
            "--storage-path", storage_path,
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

        # Build the initial cache and run the one-shot ID migration BEFORE
        # subscribing to events, so migration never runs on the hot path.
        self._update_cache()
        self._run_id_migration_once(self.cached_devices)

        self.client.subscribe_events(self._on_event, EventType.ATTRIBUTE_UPDATED)

        if fabric_label:
            try:
                await self.client.send_command("set_default_fabric_label", label=fabric_label)
                logging.info(f"Fabric label set to: {fabric_label}")
            except Exception as e:
                logging.warning(f"Failed to set fabric label: {e}")

        logging.info("Matter bridge is fully operational.")

    async def shutdown(self, app):
        # Flush any pending debounced devices_cache write before tearing down.
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
        self._flush_devices_cache()
        if self.listen_task:
            self.listen_task.cancel()
        if self.session:
            await self.session.close()
        if self.process:
            self.process.terminate()
            await self.process.wait()

    def is_ready(self) -> bool:
        return self.client is not None

    # -- Public facade (core/server never touch privates) --------------------

    def sync(self) -> None:
        """Public wrapper for the internal cache rebuild."""
        self._update_cache()

    def names_for(self, device_id: str) -> list:
        return self.device_names.get(device_id, [])

    def add_alias(self, device_id: str, name: str) -> list:
        """Assign an alias, enforcing global uniqueness. Returns the new list.

        Encapsulates the conflict check that used to live in core.set_name."""
        for existing_id, names in self.device_names.items():
            if name in names and existing_id != device_id:
                raise ValueError(
                    "Name conflict: Alias already assigned to another device"
                )
        self.device_names.setdefault(device_id, [])
        if name not in self.device_names[device_id]:
            self.device_names[device_id].append(name)
            self._save_names_cache()
        return self.device_names[device_id]

    def remove_alias(self, device_id: str, name: str) -> list:
        names = self.device_names.get(device_id, [])
        if name not in names:
            raise KeyError(f"Alias '{name}' not found on device {device_id}")
        names.remove(name)
        if not names:
            del self.device_names[device_id]
        self._save_names_cache()
        return self.device_names.get(device_id, [])

    def device_ids_for_node(self, node_id: int) -> list[str]:
        return [d["id"] for d in self.cached_devices if d.get("node_id") == node_id]

    def occupancy_last_active(self, device_id: str):
        return self.occupancy_history.get(device_id)

    def subscribe_occupancy(self, device_id: str) -> "asyncio.Queue":
        queue: asyncio.Queue = asyncio.Queue(maxsize=OCCUPANCY_QUEUE_MAXSIZE)
        self.occupancy_subscribers.setdefault(device_id, []).append(queue)
        return queue

    def unsubscribe(self, queue) -> None:
        """Remove a subscriber queue and drop the id key when its list empties."""
        for device_id, subs in list(self.occupancy_subscribers.items()):
            if queue in subs:
                subs.remove(queue)
            if not subs:
                self.occupancy_subscribers.pop(device_id, None)

    def prune_stale_occupancy(self) -> None:
        """Drop occupancy history/subscribers for devices no longer in the cache."""
        live = {d["id"] for d in self.cached_devices}
        removed = [k for k in list(self.occupancy_history) if k not in live]
        for k in removed:
            self.occupancy_history.pop(k, None)
        for k in [k for k in list(self.occupancy_subscribers) if k not in live]:
            self.occupancy_subscribers.pop(k, None)
        if removed:
            try:
                self._save_json(paths.occupancy_cache(), self.occupancy_history)
            except OSError:
                pass

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

    async def dedupe_by_unique_id(self, new_node_id: int) -> list[int]:
        """Unpair any older fabric node that shares endpoint-0 UniqueID with new_node_id.

        Aqara/Eve hubs keep the same UniqueID across re-pairings, so a re-commission
        leaves a phantom node and produces duplicate dev_* IDs. Called automatically
        after register_device(); also exposed via /api/unregister for manual cleanup.
        """
        new_node = next(
            (n for n in self.client.get_nodes() if n.node_id == new_node_id), None
        )
        if not new_node or 0 not in new_node.endpoints:
            return []

        new_uid = (
            new_node.get_attribute_value(0, 40, 18)
            or new_node.get_attribute_value(0, 40, 15)
        )
        if not new_uid:
            return []

        removed = []
        for node in list(self.client.get_nodes()):
            if node.node_id == new_node_id or 0 not in node.endpoints:
                continue
            old_uid = (
                node.get_attribute_value(0, 40, 18)
                or node.get_attribute_value(0, 40, 15)
            )
            if old_uid == new_uid:
                logging.warning(
                    f"Duplicate UniqueID {old_uid} on node {node.node_id} — unpairing"
                )
                try:
                    await self.client.send_command("remove_node", node_id=node.node_id)
                    removed.append(node.node_id)
                except Exception as e:
                    logging.error(f"Failed to unpair node {node.node_id}: {e}")

        if removed:
            self._update_cache()
            self.prune_stale_occupancy()
        return removed

    def _run_id_migration_once(self, devices: list[dict]) -> None:
        """Run the legacy->stable ID migration exactly once.

        Gated by a persisted schema marker so a completed migration is never
        re-attempted (it used to run on every ATTRIBUTE_UPDATED).
        """
        marker = self._load_json(paths.schema_marker(), {})
        if marker.get("device_id_format") == SCHEMA_VERSION:
            return
        self._migrate_ids(devices)
        self._save_json(paths.schema_marker(), {"device_id_format": SCHEMA_VERSION})

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
        self._save_json(paths.occupancy_cache(), self.occupancy_history)

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

                # Thermostat cluster (Aqara hubs expose IR ACs as Matter Thermostats)
                if 513 in endpoint.clusters:
                    for attr_id, key in (
                        (0, "local_temperature"),
                        (17, "cooling_setpoint"),
                        (18, "heating_setpoint"),
                        (28, "system_mode"),
                    ):
                        v = node.get_attribute_value(ep_id, 513, attr_id)
                        if v is not None:
                            states[key] = int(v)

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
                                        try:
                                            q.put_nowait((cur, ts))
                                        except asyncio.QueueFull:
                                            pass  # slow SSE consumer; drop event

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

        self.cached_devices = devices
        self._schedule_flush()

        if occupancy_updated:
            try:
                self._save_json(paths.occupancy_cache(), self.occupancy_history)
            except OSError:
                pass  # logged in _save_json; not on the critical read path
