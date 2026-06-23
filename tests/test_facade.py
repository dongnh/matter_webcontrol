"""Step 6: the bridge public facade (aliases, node lookup, occupancy) and the
G3 pruning / SSE-key cleanup, tested against the real MatterBridgeServer."""

import pytest

from cli.matter_bridge import OCCUPANCY_QUEUE_MAXSIZE, MatterBridgeServer


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return MatterBridgeServer(8080)


# -- alias facade -----------------------------------------------------------


def test_add_alias_conflict(bridge):
    bridge.add_alias("dev_a", "Lamp")
    with pytest.raises(ValueError):
        bridge.add_alias("dev_b", "Lamp")


def test_add_alias_same_device_idempotent(bridge):
    bridge.add_alias("dev_a", "Lamp")
    assert bridge.add_alias("dev_a", "Lamp") == ["Lamp"]


def test_remove_alias_missing(bridge):
    with pytest.raises(KeyError):
        bridge.remove_alias("dev_a", "Nope")


def test_remove_alias_clears_empty_key(bridge):
    bridge.add_alias("dev_a", "Lamp")
    assert bridge.remove_alias("dev_a", "Lamp") == []
    assert "dev_a" not in bridge.device_names


# -- node lookup ------------------------------------------------------------


def test_device_ids_for_node(bridge):
    bridge.cached_devices = [
        {"id": "dev_a", "node_id": 1, "endpoint_id": 1, "states": {}},
        {"id": "dev_b", "node_id": 1, "endpoint_id": 2, "states": {}},
        {"id": "dev_c", "node_id": 2, "endpoint_id": 1, "states": {}},
    ]
    assert sorted(bridge.device_ids_for_node(1)) == ["dev_a", "dev_b"]


# -- occupancy facade + pruning (G3) ----------------------------------------


def test_prune_stale_occupancy(bridge):
    bridge.cached_devices = [
        {"id": "dev_live", "node_id": 1, "endpoint_id": 1, "states": {}}
    ]
    bridge.occupancy_history = {"dev_live": 1, "dev_gone": 2}
    bridge.occupancy_subscribers = {"dev_live": [], "dev_gone": []}
    bridge.prune_stale_occupancy()
    assert "dev_gone" not in bridge.occupancy_history
    assert "dev_gone" not in bridge.occupancy_subscribers
    assert "dev_live" in bridge.occupancy_history


@pytest.mark.asyncio
async def test_subscribe_is_bounded_and_unsubscribe_cleans_key(bridge):
    q = bridge.subscribe_occupancy("dev_a")
    assert q.maxsize == OCCUPANCY_QUEUE_MAXSIZE
    assert "dev_a" in bridge.occupancy_subscribers
    bridge.unsubscribe(q)
    assert "dev_a" not in bridge.occupancy_subscribers  # empty key deleted (G3)


# -- controller wiring through the facade -----------------------------------


def test_controller_set_remove_name_via_facade(controller_with_fixture):
    ctrl, _bridge = controller_with_fixture("A")
    assert ctrl.set_name("dev_aaaa0001", "Lamp")["names"] == ["Lamp"]
    dev = next(d for d in ctrl.get_devices() if d["id"] == "dev_aaaa0001")
    assert dev["names"] == ["Lamp"]
    assert ctrl.remove_name("dev_aaaa0001", "Lamp")["names"] == []
