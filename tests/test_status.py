"""Step 5: get_status counts agree with the list endpoints (both go through
the one deduped enumerator) and survive a federation loop."""

from tests.fakes import StubLogicalClient


def test_status_counts_match_list_endpoints(controller_with_fixture):
    ctrl, _bridge = controller_with_fixture("A")
    status = ctrl.get_status()
    assert status["total_devices"] == len(ctrl.get_devices())
    assert status["lights_on"] + status["lights_off"] == len(ctrl.get_lights())


def test_status_light_split(controller_with_fixture):
    ctrl, _bridge = controller_with_fixture("A")
    status = ctrl.get_status()
    assert status["lights_on"] == 1  # dev_aaaa0001
    assert status["lights_off"] == 1  # dev_aaaa0002
    assert status["sensors_active"] == 1  # dev_aaaa0003 (occupancy)


def test_status_dedup_on_full_federation_loop(controller_with_fixture, logical_manager):
    # A peer re-exports every one of A's own devices (a mutual-federation loop).
    loop = StubLogicalClient(
        "peer:1",
        [
            {
                "id": "dev_aaaa0001",
                "node_id": "peer:1",
                "endpoint_id": 1,
                "states": {"on_off": True, "brightness_raw": 200},
            },
            {
                "id": "dev_aaaa0002",
                "node_id": "peer:1",
                "endpoint_id": 2,
                "states": {"on_off": False},
            },
            {
                "id": "dev_aaaa0003",
                "node_id": "peer:1",
                "endpoint_id": 3,
                "states": {"occupancy": 0},
            },
        ],
    )
    ctrl, _bridge = controller_with_fixture("A", logical=logical_manager(loop))
    status = ctrl.get_status()
    assert status["total_devices"] == 3  # not 6
    assert status["lights_on"] == 1
    assert status["lights_off"] == 1


def test_status_acs_counted(controller_with_fixture):
    ctrl, _bridge = controller_with_fixture("ac")
    status = ctrl.get_status()
    assert status["acs_on"] == 1  # fixture "ac" Cool-mode AC
    assert status["acs_off"] == 0
