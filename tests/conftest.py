"""Shared pytest fixtures.

Two entry points:
  * ``controller_with_fixture`` — a plain ``DeviceController`` over a FakeBridge,
    for unit-testing core logic.
  * ``make_client`` / ``client`` — an httpx ``AsyncClient`` driving the real
    FastAPI app in-process via ``ASGITransport`` (no socket, no port binding),
    so the HTTP edge is exercised without colliding with a running server.
"""

import tempfile

import httpx
import pytest
import pytest_asyncio

from cli.core import DeviceController
from cli.logic_bridge import LogicalBridgeManager
from tests.fakes import FakeBridge

TEST_KEY = "testkey"


def _fresh_logical() -> LogicalBridgeManager:
    """A manager whose persistence lands in a throwaway temp file."""
    tmp = tempfile.NamedTemporaryFile(
        prefix="bridge_cache_test_", suffix=".json", delete=False
    )
    tmp.close()
    return LogicalBridgeManager(cache_file=tmp.name)


@pytest.fixture
def logical_manager():
    """Factory: build a LogicalBridgeManager pre-populated with stub clients."""

    def _make(*clients) -> LogicalBridgeManager:
        mgr = _fresh_logical()
        for c in clients:
            mgr.registry[c.node_id] = c
        return mgr

    return _make


@pytest.fixture
def controller_with_fixture():
    """Factory: (fixture_name, logical?) -> (DeviceController, FakeBridge)."""

    def _make(fixture: str = "A", logical: LogicalBridgeManager | None = None):
        bridge = FakeBridge(fixture)
        mgr = logical or _fresh_logical()
        return DeviceController(bridge, mgr), bridge

    return _make


@pytest.fixture
def make_client():
    """Factory: build an AsyncClient bound to the app with a given fixture.

    Returns (client, bridge, controller). Caller is responsible for nothing —
    the transport is closed by garbage collection at test end; tests that need
    explicit close can ``await client.aclose()``.
    """
    from cli import server as srv

    created: list[httpx.AsyncClient] = []

    def _make(
        fixture: str = "A",
        api_key: str | None = TEST_KEY,
        logical: LogicalBridgeManager | None = None,
    ):
        bridge = FakeBridge(fixture)
        mgr = logical or _fresh_logical()
        controller = DeviceController(bridge, mgr)
        srv.controller = controller
        srv.app.state.api_key = api_key
        headers = {"X-API-Key": api_key} if api_key else {}
        transport = httpx.ASGITransport(app=srv.app)
        client = httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=headers
        )
        created.append(client)
        return client, bridge, controller

    yield _make


@pytest_asyncio.fixture
async def client(make_client):
    """Default client: fixture A, auth on."""
    c, _bridge, _controller = make_client("A")
    try:
        yield c
    finally:
        await c.aclose()
