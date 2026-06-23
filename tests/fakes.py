"""Single source of in-memory fakes for tests and the dev harness.

`dev/fake_server.py` and the pytest suite both import from here so the fake
Matter bridge has exactly one definition. The fake mirrors the public surface
that ``DeviceController`` consumes from ``MatterBridgeServer`` (including the
bridge facade added in the restructure), so controller logic can be exercised
without real Matter hardware.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Fixtures: pre-baked device sets so each instance has a distinct topology     #
# --------------------------------------------------------------------------- #

FIXTURES: dict[str, list[dict]] = {
    "A": [
        {
            "id": "dev_aaaa0001", "node_id": 1, "endpoint_id": 1,
            "states": {"on_off": True, "brightness_raw": 200},
        },
        {
            "id": "dev_aaaa0002", "node_id": 1, "endpoint_id": 2,
            "states": {"on_off": False, "brightness_raw": 0, "color_temp_mireds": 320},
        },
        {
            "id": "dev_aaaa0003", "node_id": 2, "endpoint_id": 1,
            "states": {"occupancy": 0},
        },
    ],
    "B": [
        {
            "id": "dev_bbbb0001", "node_id": 10, "endpoint_id": 1,
            "states": {"on_off": True, "brightness_raw": 100, "color_temp_mireds": 250},
        },
        {
            "id": "dev_bbbb0002", "node_id": 11, "endpoint_id": 1,
            "states": {"temperature": 23, "humidity": 55},
        },
    ],
    # Physical AC (Thermostat cluster) for set_ac / get_ac tests.
    "ac": [
        {
            "id": "dev_ac000001", "node_id": 5, "endpoint_id": 1,
            "states": {
                "system_mode": 3,            # Cool
                "local_temperature": 2500,   # 25.00 °C
                "cooling_setpoint": 2600,    # 26.00 °C
                "heating_setpoint": 2000,    # 20.00 °C
            },
        },
    ],
    "empty": [],
}


# Maps a Thermostat write attribute path to its cached state key.
_THERMO_ATTR_KEYS = {
    (513, 28): "system_mode",
    (513, 17): "cooling_setpoint",
    (513, 18): "heating_setpoint",
}


# --------------------------------------------------------------------------- #
# Fake Matter client — minimal surface DeviceController needs                  #
# --------------------------------------------------------------------------- #

class FakeMatterClient:
    """Stand-in for matter_server.client.MatterClient."""

    def __init__(self, bridge: "FakeBridge"):
        self._bridge = bridge
        self.writes: list[tuple[int, str, Any]] = []
        self.commands: list[tuple[str, dict]] = []

    def _device_at(self, node_id: int, endpoint_id: int) -> dict | None:
        for dev in self._bridge.cached_devices:
            if dev["node_id"] == node_id and dev["endpoint_id"] == endpoint_id:
                return dev
        return None

    async def send_device_command(self, node_id: int, endpoint_id: int, cmd: Any) -> None:
        dev = self._device_at(node_id, endpoint_id)
        if dev is None:
            return
        cls = type(cmd).__name__
        if cls == "Off":
            dev["states"]["on_off"] = False
        elif cls == "MoveToLevelWithOnOff":
            lvl = getattr(cmd, "level", 0)
            dev["states"]["brightness_raw"] = lvl
            dev["states"]["on_off"] = lvl > 0
        elif cls == "MoveToColorTemperature":
            dev["states"]["color_temp_mireds"] = getattr(cmd, "colorTemperatureMireds", 0)
        logger.info("[fake] cmd %s on %s -> %s", cls, dev["id"], dev["states"])

    async def write_attribute(self, node_id: int, attribute_path: str, value: Any) -> None:
        """Record + apply a Thermostat attribute write (ep/cluster/attr)."""
        self.writes.append((node_id, attribute_path, value))
        try:
            ep_s, cluster_s, attr_s = attribute_path.split("/")
            ep_id, cluster, attr = int(ep_s), int(cluster_s), int(attr_s)
        except ValueError:
            return
        key = _THERMO_ATTR_KEYS.get((cluster, attr))
        dev = self._device_at(node_id, ep_id)
        if dev is not None and key is not None:
            dev["states"][key] = int(value)
        logger.info("[fake] write %s=%s on node %s", attribute_path, value, node_id)

    async def send_command(self, name: str, **kwargs) -> Any:
        self.commands.append((name, kwargs))
        logger.info("[fake] send_command(%s, %s)", name, kwargs)
        return self._bridge.command_responses.get(name)

    def get_nodes(self) -> list:
        return []


# --------------------------------------------------------------------------- #
# Fake bridge — drop-in replacement for MatterBridgeServer                     #
# --------------------------------------------------------------------------- #

class FakeBridge:
    """No-Matter-dependency stand-in implementing the bridge public facade."""

    def __init__(self, fixture: str):
        self.cached_devices = [
            dict(d, states=dict(d["states"])) for d in FIXTURES[fixture]
        ]
        self.device_names: dict[str, list[str]] = {}
        self.occupancy_history: dict[str, int] = {}
        self.occupancy_subscribers: dict[str, list] = {}
        self.client = FakeMatterClient(self)
        # Test hook: canned responses for client.send_command(name).
        self.command_responses: dict[str, Any] = {}

    # -- readiness / sync ---------------------------------------------------

    def is_ready(self) -> bool:
        return True

    def _update_cache(self) -> None:
        pass  # no-op; fixture is static

    def sync(self) -> None:
        self._update_cache()

    def _save_names_cache(self) -> None:
        pass  # don't pollute disk during tests

    # -- alias facade -------------------------------------------------------

    def names_for(self, device_id: str) -> list:
        return self.device_names.get(device_id, [])

    def add_alias(self, device_id: str, name: str) -> list:
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

    # -- occupancy facade ---------------------------------------------------

    def occupancy_last_active(self, device_id: str):
        return self.occupancy_history.get(device_id)

    def subscribe_occupancy(self, device_id: str):
        import asyncio
        queue: asyncio.Queue = asyncio.Queue()
        self.occupancy_subscribers.setdefault(device_id, []).append(queue)
        return queue

    def unsubscribe(self, queue) -> None:
        for device_id, subs in list(self.occupancy_subscribers.items()):
            if queue in subs:
                subs.remove(queue)
            if not subs:
                self.occupancy_subscribers.pop(device_id, None)

    # -- node facade --------------------------------------------------------

    def device_ids_for_node(self, node_id: int) -> list[str]:
        return [d["id"] for d in self.cached_devices if d.get("node_id") == node_id]
