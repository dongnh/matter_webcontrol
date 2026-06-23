"""Step 5: routing (_route) is logical-first, enumeration (_iter_devices)
dedups by id once, and the two stay consistent."""

import pytest

from tests.fakes import StubLogicalClient


def _dup_light(device_id="dev_aaaa0001", node="peer:1"):
    return StubLogicalClient(
        node,
        [
            {
                "id": device_id,
                "node_id": node,
                "endpoint_id": 1,
                "states": {"on_off": True, "brightness_raw": 50},
            }
        ],
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
        [
            {
                "id": "dev_ac000001",
                "node_id": "peer:1",
                "endpoint_id": 1,
                "states": {"system_mode": 3},
            }
        ],
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


# -- Step 7: set_ac setpoint-by-mode (C2/C3/E3/API2) ------------------------


@pytest.mark.asyncio
async def test_set_ac_heat_mode_writes_heating_setpoint(controller_with_fixture):
    ctrl, bridge = controller_with_fixture("ac")  # physical AC, currently Cool
    result = await ctrl.set_ac("dev_ac000001", mode=4, setpoint=22.0)  # Heat
    paths = [w[1] for w in bridge.client.writes]
    assert "1/513/18" in paths  # heating_setpoint
    assert "1/513/17" not in paths  # NOT cooling_setpoint
    assert result["wrote"]["setpoint"] == 2200  # neutral key (C3)


@pytest.mark.asyncio
async def test_set_ac_cool_mode_writes_cooling_setpoint(controller_with_fixture):
    ctrl, bridge = controller_with_fixture("ac")  # current mode Cool(3)
    result = await ctrl.set_ac("dev_ac000001", setpoint=24.0)  # effective = current
    assert "1/513/17" in [w[1] for w in bridge.client.writes]
    assert result["wrote"]["setpoint"] == 2400


@pytest.mark.asyncio
async def test_set_ac_fan_speed_rejected_on_physical(controller_with_fixture):
    ctrl, _bridge = controller_with_fixture("ac")
    with pytest.raises(ValueError):
        await ctrl.set_ac("dev_ac000001", fan_speed=50)  # API2


@pytest.mark.asyncio
async def test_set_ac_auto_single_setpoint_rejected(controller_with_fixture):
    ctrl, _bridge = controller_with_fixture("ac")
    with pytest.raises(ValueError):
        await ctrl.set_ac("dev_ac000001", mode=1, setpoint=24.0)  # Auto is ambiguous


@pytest.mark.asyncio
async def test_set_ac_partial_write_reported(controller_with_fixture, monkeypatch):
    ctrl, bridge = controller_with_fixture("ac")
    orig = bridge.client.write_attribute

    async def flaky(node_id, attribute_path, value):
        if attribute_path.endswith(("/17", "/18")):
            raise RuntimeError("setpoint write failed")
        return await orig(node_id, attribute_path, value)

    monkeypatch.setattr(bridge.client, "write_attribute", flaky)
    result = await ctrl.set_ac("dev_ac000001", mode=3, setpoint=24.0)
    assert result["status"] == "partial"
    assert "system_mode" in result["wrote"]  # mode write landed
    assert "setpoint" in result["failed"]  # setpoint write reported failed


@pytest.mark.asyncio
async def test_logical_set_ac_neutral_key_and_on(
    controller_with_fixture, logical_manager
):
    client = StubLogicalClient(
        "peer:1",
        [
            {
                "id": "dev_lac",
                "node_id": "peer:1",
                "endpoint_id": 1,
                "states": {"system_mode": 3},
            }
        ],
    )
    ctrl, _bridge = controller_with_fixture("empty", logical=logical_manager(client))

    r = await ctrl.set_ac("dev_lac", setpoint=26.0)
    assert r["via"] == "logical"
    assert r["wrote"]["setpoint"] == 2600  # neutral key, not heating_setpoint (C3)
    assert "heating_setpoint" not in r["wrote"]

    r2 = await ctrl.set_ac("dev_lac", on=True)
    assert r2["wrote"]["system_mode"] == 3  # C4: no longer omitted


# -- Step 7: register_device names the right device (C1/A6/E7) ---------------


@pytest.mark.asyncio
async def test_register_device_names_exact_node(controller_with_fixture):
    ctrl, bridge = controller_with_fixture("ac")  # dev_ac000001 is on node_id 5
    bridge.command_responses["commission_with_code"] = {"node_id": 5}
    result = await ctrl.register_device("1234-567-8901", name="Bedroom AC")
    assert result["assigned_id"] == "dev_ac000001"
    assert result["name_not_applied"] is False
    assert bridge.names_for("dev_ac000001") == ["Bedroom AC"]


@pytest.mark.asyncio
async def test_register_device_name_not_applied(controller_with_fixture):
    ctrl, bridge = controller_with_fixture("ac")
    bridge.command_responses["commission_with_code"] = {
        "node_id": 999
    }  # no such device
    result = await ctrl.register_device("1234-567-8901", name="Ghost")
    assert result["assigned_id"] is None
    assert result["name_not_applied"] is True


def test_logical_client_set_ac_uses_canonical_mode():
    # The federation wire must use the canonical `mode` field, not its alias
    # `system_mode` (API4), so it also works against older peers.
    from cli.logic_bridge import LogicalBridgeClient

    client = LogicalBridgeClient("127.0.0.1", 9, None)
    captured: dict = {}
    client._request = lambda path, method="GET", query=None, body=None: captured.update(  # type: ignore[method-assign]
        body or {}
    )
    client.set_ac("dev_x", on=True, mode=4, setpoint=22.0)
    assert captured.get("mode") == 4
    assert "system_mode" not in captured
