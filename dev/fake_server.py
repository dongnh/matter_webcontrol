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

import uvicorn

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import server as srv  # noqa: E402
from cli.core import DeviceController  # noqa: E402
from cli.logic_bridge import LogicalBridgeManager  # noqa: E402
from tests.fakes import FIXTURES, FakeBridge  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


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
        logging.info(
            f"[fake] ready with fixture={fixture}, devices={len(bridge.cached_devices)}"
        )
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
