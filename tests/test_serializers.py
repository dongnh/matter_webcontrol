"""Tests for cli.serializers — pure (device, names) builders + name merge."""

from cli import serializers as s


# -- resolved_names ---------------------------------------------------------

def test_resolved_names_local_first_then_remote():
    dev = {"id": "d", "names": ["Remote A", "Shared"]}
    assert s.resolved_names(dev, ["Local"]) == ["Local", "Remote A", "Shared"]


def test_resolved_names_dedup():
    dev = {"id": "d", "names": ["Shared"]}
    assert s.resolved_names(dev, ["Shared"]) == ["Shared"]


def test_resolved_names_physical_only_local():
    dev = {"id": "d"}  # physical device dict carries no "names"
    assert s.resolved_names(dev, ["Lamp"]) == ["Lamp"]


# -- build_light (C5: off keeps the stored brightness) ----------------------

def test_build_light_on():
    dev = {"id": "x", "states": {"on_off": True, "brightness_raw": 200}}
    assert s.build_light(dev, []) == {
        "id": "x", "names": [], "state": True, "brightness": 0.79,
    }


def test_build_light_off_keeps_brightness():
    # C5: a light that is off still reports its stored level; state conveys off.
    dev = {"id": "x", "states": {"on_off": False, "brightness_raw": 200}}
    entry = s.build_light(dev, [])
    assert entry["state"] is False
    assert entry["brightness"] == 0.79  # NOT forced to 0.0 anymore


def test_build_light_color_temp():
    dev = {"id": "x", "states": {"on_off": True, "color_temp_mireds": 320}}
    assert s.build_light(dev, [])["temperature"] == 3125


def test_build_light_skips_zero_mireds():
    dev = {"id": "x", "states": {"on_off": True, "color_temp_mireds": 0}}
    assert "temperature" not in s.build_light(dev, [])


def test_build_light_none_without_clusters():
    assert s.build_light({"id": "x", "states": {"occupancy": 1}}, []) is None


# -- build_sensor -----------------------------------------------------------

def test_build_sensor_filters_keys():
    dev = {"id": "x", "states": {"occupancy": 1, "on_off": True, "illuminance": 42}}
    entry = s.build_sensor(dev, ["Sensor"])
    assert entry == {"id": "x", "names": ["Sensor"], "occupancy": 1, "illuminance": 42}


def test_build_sensor_occupancy_timestamp():
    dev = {"id": "x", "states": {"occupancy": 1}}
    entry = s.build_sensor(dev, [], occupancy_ts=0)  # falsy ts -> no field
    assert "occupancy_last_active" not in entry
    entry2 = s.build_sensor(dev, [], occupancy_ts=1_700_000_000)
    assert "occupancy_last_active" in entry2


def test_build_sensor_none_without_sensor_clusters():
    assert s.build_sensor({"id": "x", "states": {"on_off": True}}, []) is None


# -- build_climate ----------------------------------------------------------

def test_build_climate_thermostat():
    dev = {"id": "x", "states": {"local_temperature": 2500, "humidity": 5500}}
    entry = s.build_climate(dev, [])
    assert entry["kind"] == "thermostat"
    assert entry["temperature"] == 25.0
    assert entry["humidity"] == 55.0


def test_build_climate_sensor():
    dev = {"id": "x", "states": {"temperature": 2300}}
    entry = s.build_climate(dev, [])
    assert entry["kind"] == "sensor"
    assert entry["temperature"] == 23.0


def test_build_climate_none():
    assert s.build_climate({"id": "x", "states": {"on_off": True}}, []) is None


# -- build_ac ---------------------------------------------------------------

def test_build_ac_full():
    dev = {
        "id": "x",
        "states": {
            "system_mode": 3, "local_temperature": 2500,
            "cooling_setpoint": 2600, "heating_setpoint": 2000, "fan_speed": 50,
        },
    }
    entry = s.build_ac(dev, ["AC"])
    assert entry == {
        "id": "x", "names": ["AC"], "system_mode": 3, "on": True,
        "local_temperature": 25.0, "cooling_setpoint": 26.0,
        "heating_setpoint": 20.0, "fan_speed": 50,
    }


def test_build_ac_off():
    assert s.build_ac({"id": "x", "states": {"system_mode": 0}}, [])["on"] is False


# -- build_metadata ---------------------------------------------------------

def test_build_metadata_thermostat():
    dev = {"id": "x", "states": {"system_mode": 3, "local_temperature": 2500}}
    entry = s.build_metadata(dev, ["Office AC"])
    assert entry["hardware_type"] == "thermostat"
    assert "ac" in entry["capabilities"]
    assert entry["name"] == "Office AC"


def test_build_metadata_light_capabilities():
    dev = {"id": "x", "states": {"on_off": True, "brightness_raw": 100, "color_temp_mireds": 250}}
    entry = s.build_metadata(dev, [])
    assert entry["hardware_type"] == "color_temperature_light"
    assert entry["capabilities"] == ["on_off", "brightness", "color_temperature"]
    assert entry["name"] == "x"  # falls back to id when unnamed


def test_build_metadata_none_for_bare_device():
    assert s.build_metadata({"id": "x", "states": {}}, []) is None
