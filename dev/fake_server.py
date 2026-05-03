"""Run the matter-srv FastAPI app with a fake Matter bridge.

Lets you exercise the REST API, auth middleware, and federation logic
without real Matter hardware or sudo.

    python dev/fake_server.py --port 8080 --api-key secret --fixture A
    python dev/fake_server.py --port 8090 --api-key secret2 --fixture B
"""

import argparse
import os
import sys
import logging
from contextlib import asynccontextmanager
from typing import Any

import uvicorn

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import server as srv  # noqa: E402
from cli.core import DeviceController  # noqa: E402
from cli.logic_bridge import LogicalBridgeManager  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


# --------------------------------------------------------------------------- #
# Fixtures: pre-baked device sets so each instance has a distinct topology    #
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
    "empty": [],
}


# --------------------------------------------------------------------------- #
# Fake bridge — minimal surface DeviceController needs                        #
# --------------------------------------------------------------------------- #

class FakeMatterClient:
    """Stand-in for matter_server.client.MatterClient."""
    def __init__(self, bridge: "FakeBridge"):
        self._bridge = bridge

    async def send_device_command(self, node_id: int, endpoint_id: int, cmd: Any) -> None:
        # Match cmd back to a device by (node_id, endpoint_id) and mutate state.
        for dev in self._bridge.cached_devices:
            if dev["node_id"] == node_id and dev["endpoint_id"] == endpoint_id:
                cls = type(cmd).__name__
                if cls == "Off":
                    dev["states"]["on_off"] = False
                elif cls == "MoveToLevelWithOnOff":
                    lvl = getattr(cmd, "level", 0)
                    dev["states"]["brightness_raw"] = lvl
                    dev["states"]["on_off"] = lvl > 0
                elif cls == "MoveToColorTemperature":
                    dev["states"]["color_temp_mireds"] = getattr(cmd, "colorTemperatureMireds", 0)
                logging.info(f"[fake] cmd {cls} on {dev['id']} → {dev['states']}")
                return

    async def send_command(self, name: str, **kwargs) -> None:
        logging.info(f"[fake] send_command({name}, {kwargs})")


class FakeBridge:
    """Drop-in replacement for MatterBridgeServer with no Matter dependency."""
    def __init__(self, fixture: str):
        self.cached_devices = [dict(d, states=dict(d["states"])) for d in FIXTURES[fixture]]
        self.device_names: dict[str, list[str]] = {}
        self.occupancy_history: dict[str, int] = {}
        self.occupancy_subscribers: dict[str, list] = {}
        self.client = FakeMatterClient(self)

    def is_ready(self) -> bool:
        return True

    def _update_cache(self) -> None:
        pass  # no-op; fixture is static

    def _save_names_cache(self) -> None:
        pass  # don't pollute disk during tests


# --------------------------------------------------------------------------- #
# Lifespan override                                                           #
# --------------------------------------------------------------------------- #

def make_lifespan(fixture: str, cache_file: str):
    @asynccontextmanager
    async def lifespan(app):
        bridge = FakeBridge(fixture)
        logical = LogicalBridgeManager(cache_file=cache_file)
        logical.load_cache()
        srv.controller = DeviceController(bridge, logical)
        logical.refresh_bridges()
        logging.info(f"[fake] ready with fixture={fixture}, devices={len(bridge.cached_devices)}")
        yield
    return lifespan


def main():
    parser = argparse.ArgumentParser(description="Fake matter-srv (no hardware)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--api-key", type=str, default=os.environ.get("MATTER_SRV_KEY"))
    parser.add_argument("--fixture", type=str, default="A", choices=list(FIXTURES))
    parser.add_argument("--cache-file", type=str, default="bridge_cache_dev.json")
    args = parser.parse_args()

    srv.app.router.lifespan_context = make_lifespan(args.fixture, args.cache_file)
    srv.app.state.api_key = args.api_key
    srv.app.state.port = args.port

    uvicorn.run(srv.app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
