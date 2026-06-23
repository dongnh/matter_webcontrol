"""Golden-value tests for the pure conversion logic, locked at current behavior.

In Step 0 these exercise the logic through ``extract_matter_pin`` and the
``DeviceController`` static builders; Step 4 extracts the math into
``cli/conversions.py`` and these golden values must still hold.
"""

import pytest

from cli.core import DeviceController, extract_matter_pin


# -- extract_matter_pin -----------------------------------------------------

def test_extract_pin_golden():
    assert extract_matter_pin("2456-515-1552") == 84472403


def test_extract_pin_strips_separators():
    assert extract_matter_pin("2456-515-1552") == extract_matter_pin("24565151552")
    assert extract_matter_pin("2456 515 1552") == 84472403


@pytest.mark.parametrize("bad", ["", "123", "abcd-efg-hijk", "2456-515-155"])
def test_extract_pin_rejects_bad(bad):
    with pytest.raises(ValueError):
        extract_matter_pin(bad)


# -- brightness normalization (off forces 0.0 today) ------------------------

def test_build_light_brightness_normalized():
    dev = {"id": "x", "states": {"on_off": True, "brightness_raw": 200}}
    entry = DeviceController._build_light(dev, [])
    assert entry["brightness"] == 0.79  # round(200/254, 2)
    assert entry["state"] is True


def test_build_light_off_forces_zero():
    dev = {
        "id": "x",
        "states": {"on_off": False, "brightness_raw": 0, "color_temp_mireds": 320},
    }
    entry = DeviceController._build_light(dev, [])
    assert entry["brightness"] == 0.0
    assert entry["state"] is False


def test_build_light_mired_to_kelvin():
    dev = {"id": "x", "states": {"on_off": True, "color_temp_mireds": 320}}
    entry = DeviceController._build_light(dev, [])
    assert entry["temperature"] == 3125  # int(1_000_000 / 320)


def test_build_light_none_when_no_light_clusters():
    assert DeviceController._build_light({"id": "x", "states": {"occupancy": 1}}, []) is None


# -- centi-degree scaling ---------------------------------------------------

def test_climate_entry_centi_scaling():
    dev = {"id": "x", "states": {"local_temperature": 2500, "humidity": 5512}}
    entry = DeviceController._climate_entry(dev, [])
    assert entry["temperature"] == 25.0
    assert entry["humidity"] == 55.12
    assert entry["kind"] == "thermostat"


def test_ac_entry_centi_and_on_flag():
    dev = {
        "id": "x",
        "states": {
            "system_mode": 3,
            "local_temperature": 2500,
            "cooling_setpoint": 2600,
            "heating_setpoint": 2000,
        },
    }
    entry = DeviceController._ac_entry(dev, [])
    assert entry["on"] is True
    assert entry["local_temperature"] == 25.0
    assert entry["cooling_setpoint"] == 26.0
    assert entry["heating_setpoint"] == 20.0


def test_ac_entry_off_when_mode_zero():
    entry = DeviceController._ac_entry({"id": "x", "states": {"system_mode": 0}}, [])
    assert entry["on"] is False
