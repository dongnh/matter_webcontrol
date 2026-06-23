"""Step 1: a slow/unreachable federation peer must not freeze the event loop,
and refresh_bridges must fan out concurrently and report per-bridge status."""

import asyncio
import time

import pytest

from cli.logic_bridge import LogicalBridgeManager


class _SlowClient:
    """Fake logical-bridge client whose calls block in a thread."""

    def __init__(self, node_id: str, sleep: float = 0.4, fail: bool = False):
        self.sleep = sleep
        self.fail = fail
        self.calls: list[tuple] = []
        self.devices = {
            "dev_log1": {
                "id": "dev_log1",
                "node_id": node_id,
                "endpoint_id": 1,
                "states": {"on_off": True, "brightness_raw": 100},
            }
        }

    def refresh(self) -> None:
        time.sleep(self.sleep)
        if self.fail:
            raise RuntimeError("unreachable")

    def set_level(self, device_id: str, level: int) -> None:
        time.sleep(self.sleep)
        self.calls.append(("set_level", device_id, level))


def _manager_with(*clients) -> LogicalBridgeManager:
    import tempfile

    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    mgr = LogicalBridgeManager(cache_file=tmp.name)
    for c in clients:
        mgr.registry[c.devices["dev_log1"]["node_id"]] = c
    return mgr


def test_refresh_bridges_reports_failures():
    ok = _SlowClient("a:1", sleep=0.0)
    bad = _SlowClient("b:1", sleep=0.0, fail=True)
    mgr = _manager_with(ok, bad)
    result = mgr.refresh_bridges()
    assert result == {"refreshed": 1, "failed": 1}


def test_refresh_bridges_runs_concurrently():
    clients = [_SlowClient(f"{i}:1", sleep=0.3) for i in range(3)]
    mgr = _manager_with(*clients)
    t0 = time.perf_counter()
    result = mgr.refresh_bridges()
    elapsed = time.perf_counter() - t0
    assert result == {"refreshed": 3, "failed": 0}
    assert elapsed < 0.7, f"serial would be ~0.9s, got {elapsed:.2f}s"


def test_refresh_bridges_empty():
    mgr = _manager_with()
    assert mgr.refresh_bridges() == {"refreshed": 0, "failed": 0}


@pytest.mark.asyncio
async def test_logical_control_does_not_block_loop(controller_with_fixture):
    slow = _SlowClient("x:1", sleep=0.5)
    mgr = _manager_with(slow)
    ctrl, _bridge = controller_with_fixture("empty", logical=mgr)

    task = asyncio.create_task(ctrl.set_level("dev_log1", 120))
    await asyncio.sleep(0)  # let the thread offload start

    t0 = time.perf_counter()
    await asyncio.sleep(0.05)  # loop stays responsive while the thread blocks
    loop_latency = time.perf_counter() - t0

    result = await task
    assert loop_latency < 0.2, f"event loop appears blocked ({loop_latency:.2f}s)"
    assert result["type"] == "logical"
    assert slow.calls == [("set_level", "dev_log1", 120)]
