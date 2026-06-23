"""Step 5: routing (_route) is logical-first, enumeration (_iter_devices)
dedups by id once, and the two stay consistent."""

import pytest

from tests.fakes import StubLogicalClient


def _dup_light(device_id="dev_aaaa0001", node="peer:1"):
    return StubLogicalClient(
        node,
        [{"id": device_id, "node_id": node, "endpoint_id": 1,
          "states": {"on_off": True, "brightness_raw": 50}}],
    )


# -- dedup (A5/G2 dedup half) -----------------------------------------------

def test_iter_devices_dedup_by_id(controller_with_fixture, logical_manager):
    # dev_aaaa0001 is physical (fixture A) AND re-exported by a peer (loop).
    client = _dup_light()
    ctrl, _bridge = controller_with_fixture("A", logical=logical_manager(client))

    ids = [d["id"] for d in ctrl.get_devices()]
    assert ids.count("dev_aaaa0001") == 1
    # Fixture A has 3 devices; the duplicate adds nothing new.
    assert ctrl.get_status()["total_devices"] == 3
    # And lights aren't double-counted either.
    assert [light["id"] for light in ctrl.get_lights()].count("dev_aaaa0001") == 1


def test_iter_devices_physical_first_identity(controller_with_fixture, logical_manager):
    # The peer reports brightness_raw 50; the physical fixture reports 200.
    # Enumeration is physical-first, so /api/devices shows the local value.
    client = _dup_light()
    ctrl, _bridge = controller_with_fixture("A", logical=logical_manager(client))
    dev = next(d for d in ctrl.get_devices() if d["id"] == "dev_aaaa0001")
    assert dev["states"]["brightness_raw"] == 200


# -- routing logical-first (A1) ---------------------------------------------

@pytest.mark.asyncio
async def test_control_routes_logical_first(controller_with_fixture, logical_manager):
    client = _dup_light()
    ctrl, bridge = controller_with_fixture("A", logical=logical_manager(client))

    result = await ctrl.set_device("dev_aaaa0001", brightness=0.5)
    assert result["type"] == "logical"
    assert ("set_brightness", "dev_aaaa0001", 0.5) in client.calls
    # The same-id physical device must NOT be written.
    phys = next(d for d in bridge.cached_devices if d["id"] == "dev_aaaa0001")
    assert phys["states"]["on_off"] is True


@pytest.mark.asyncio
async def test_control_physical_when_no_logical(controller_with_fixture):
    ctrl, bridge = controller_with_fixture("A")
    result = await ctrl.set_device("dev_aaaa0001", brightness=0.0)
    assert result["type"] == "physical"
    phys = next(d for d in bridge.cached_devices if d["id"] == "dev_aaaa0001")
    assert phys["states"]["on_off"] is False


@pytest.mark.asyncio
async def test_set_ac_routes_logical_first(controller_with_fixture, logical_manager):
    # Same-id AC physical (fixture "ac") + logical (peer). set_ac must forward
    # to the logical bridge — previously set_device's AC branch was physical-first.
    client = StubLogicalClient(
        "peer:1",
        [{"id": "dev_ac000001", "node_id": "peer:1", "endpoint_id": 1,
          "states": {"system_mode": 3}}],
    )
    ctrl, _bridge = controller_with_fixture("ac", logical=logical_manager(client))

    result = await ctrl.set_ac("dev_ac000001", on=True)
    assert result["via"] == "logical"
    assert client.calls and client.calls[0][0] == "set_ac"


@pytest.mark.asyncio
async def test_toggle_unknown_device_raises(controller_with_fixture):
    ctrl, _bridge = controller_with_fixture("A")
    with pytest.raises(KeyError):
        await ctrl.toggle("dev_nope")
