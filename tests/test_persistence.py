"""Step 2/3: devices_cache writes are debounced + skip-unchanged, the ID
migration runs once behind a marker, and writes are atomic (Step 3)."""

import json
import os

import pytest

from cli.core import DeviceController
from cli.logic_bridge import LogicalBridgeManager
from cli.matter_bridge import MatterBridgeServer


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    """A MatterBridgeServer with caches rooted in a throwaway dir (no connect)."""
    monkeypatch.chdir(tmp_path)
    return MatterBridgeServer(8080)


# -- Step 2: debounced flush -------------------------------------------------


def test_flush_skips_unchanged(bridge, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(bridge, "_save_json", lambda path, data: calls.append(path))

    bridge.cached_devices = [
        {"id": "dev_x", "node_id": 1, "endpoint_id": 1, "states": {"on_off": True}}
    ]
    bridge._flush_devices_cache()
    assert len(calls) == 1  # first write

    bridge._flush_devices_cache()
    assert len(calls) == 1  # unchanged snapshot -> no write

    bridge.cached_devices[0]["states"]["on_off"] = False
    bridge._flush_devices_cache()
    assert len(calls) == 2  # changed -> write


def test_schedule_flush_inline_without_loop(bridge):
    """With no running loop, _schedule_flush writes inline (no event needed)."""
    bridge.cached_devices = [
        {"id": "dev_y", "node_id": 1, "endpoint_id": 1, "states": {"on_off": True}}
    ]
    bridge._schedule_flush()
    with open("devices_cache.json", encoding="utf-8") as f:
        written = json.load(f)
    assert written[0]["id"] == "dev_y"


# -- Step 2: one-shot ID migration ------------------------------------------


def test_id_migration_runs_once(bridge):
    bridge.device_names = {"dev_1_1": ["Old Name"]}
    devices = [{"id": "dev_abc12345", "node_id": 1, "endpoint_id": 1, "states": {}}]

    bridge._run_id_migration_once(devices)
    assert bridge.device_names.get("dev_abc12345") == ["Old Name"]
    assert "dev_1_1" not in bridge.device_names

    # Marker is set; a second run must be a no-op even if legacy keys reappear.
    bridge.device_names["dev_1_1"] = ["Should NOT migrate again"]
    bridge._run_id_migration_once(devices)
    assert bridge.device_names.get("dev_1_1") == ["Should NOT migrate again"]
    assert bridge.device_names.get("dev_abc12345") == ["Old Name"]


# -- Step 3: atomic + loud persistence --------------------------------------


def test_save_json_atomic_no_tmp_left(bridge, tmp_path):
    p = str(tmp_path / "x.json")
    bridge._save_json(p, {"v": 1})
    bridge._save_json(p, {"v": 2})
    with open(p, encoding="utf-8") as f:
        assert json.load(f) == {"v": 2}
    assert not os.path.exists(p + ".tmp")


def test_save_json_reraises_oserror(bridge, tmp_path):
    bad = str(tmp_path / "missing_dir" / "x.json")  # parent does not exist
    with pytest.raises(OSError):
        bridge._save_json(bad, {"v": 1})


def test_failed_write_leaves_original_intact(bridge, tmp_path, monkeypatch):
    p = str(tmp_path / "x.json")
    bridge._save_json(p, {"v": 1})

    import cli.matter_bridge as mb

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(mb.json, "dump", boom)
    with pytest.raises(OSError):
        bridge._save_json(p, {"v": 2})

    with open(p, encoding="utf-8") as f:
        assert json.load(f) == {"v": 1}  # crash mid-write left the old file intact
    assert not os.path.exists(p + ".tmp")  # partial tmp cleaned up


def test_set_name_fails_loud_on_write_error(bridge, monkeypatch, tmp_path):
    ctrl = DeviceController(
        bridge, LogicalBridgeManager(cache_file=str(tmp_path / "bridge.json"))
    )
    bridge.cached_devices = [
        {"id": "dev_x", "node_id": 1, "endpoint_id": 1, "states": {"on_off": True}}
    ]

    def boom(*a, **k):
        raise OSError("read-only fs")

    monkeypatch.setattr(bridge, "_save_json", boom)
    with pytest.raises(OSError):
        ctrl.set_name("dev_x", "Lamp")  # must NOT silently report success
