"""Pure ``(device, names) -> response-dict`` builders plus the single
name-merge policy.

These output dicts are the federation wire format peers consume, so the shapes
are deliberately stable (no typed dataclasses). Every builder returns ``None``
when the device has no clusters relevant to that view.
"""

import datetime

from cli import conversions as conv
from cli.constants import SENSOR_KEYS


def resolved_names(dev: dict, local_names: list | None) -> list:
    """The one alias-merge policy: locally-assigned aliases first, then any
    names carried on a remote (logical) device dict, de-duplicated."""
    merged = list(local_names or [])
    for n in dev.get("names", []):
        if n not in merged:
            merged.append(n)
    return merged


def build_light(device: dict, names: list) -> dict | None:
    states = device.get("states", {})
    if "on_off" not in states and "brightness_raw" not in states:
        return None
    entry = {
        "id": device["id"],
        "names": names,
        "state": states.get("on_off"),
        "brightness": conv.normalize_brightness(states.get("brightness_raw")),
    }
    kelvin = conv.mireds_to_kelvin(states.get("color_temp_mireds"))
    if kelvin is not None:
        entry["temperature"] = kelvin
    return entry


def build_sensor(device: dict, names: list, occupancy_ts: int | None = None) -> dict | None:
    states = device.get("states", {})
    data = {k: states[k] for k in SENSOR_KEYS if k in states}
    if not data:
        return None
    if "occupancy" in data and occupancy_ts:
        data["occupancy_last_active"] = datetime.datetime.fromtimestamp(
            occupancy_ts
        ).strftime("%Y-%m-%d %H:%M:%S")
    return {"id": device["id"], "names": names, **data}


def build_climate(device: dict, names: list) -> dict | None:
    states = device.get("states", {})
    temp_c = None
    humidity = None
    kind = None

    if "local_temperature" in states:
        temp_c = conv.centi_to_unit(states["local_temperature"])
        kind = "thermostat"
    elif "temperature" in states:
        temp_c = conv.centi_to_unit(states["temperature"])
        kind = "sensor"

    if "humidity" in states:
        humidity = conv.centi_to_unit(states["humidity"])
        kind = kind or "sensor"

    if temp_c is None and humidity is None:
        return None

    out = {"id": device["id"], "names": names, "kind": kind}
    if temp_c is not None:
        out["temperature"] = temp_c
    if humidity is not None:
        out["humidity"] = humidity
    return out


def build_ac(device: dict, names: list) -> dict:
    s = device.get("states", {})
    out = {
        "id": device["id"],
        "names": names,
        "system_mode": s.get("system_mode"),
        "on": bool(s.get("system_mode")),  # 0=Off -> False; any non-zero -> True
    }
    if "local_temperature" in s:
        out["local_temperature"] = conv.centi_to_unit(s["local_temperature"])
    if "cooling_setpoint" in s:
        out["cooling_setpoint"] = conv.centi_to_unit(s["cooling_setpoint"])
    if "heating_setpoint" in s:
        out["heating_setpoint"] = conv.centi_to_unit(s["heating_setpoint"])
    if "fan_speed" in s:
        out["fan_speed"] = int(s["fan_speed"])
    return out


def build_metadata(device: dict, names: list) -> dict | None:
    """Declarative capability + hardware-type entry for /api/metadata."""
    states = device.get("states", {})

    capabilities = []
    if "on_off" in states:
        capabilities.append("on_off")
    if "brightness_raw" in states:
        capabilities.append("brightness")
    if "color_temp_mireds" in states:
        capabilities.append("color_temperature")
    if "occupancy" in states:
        capabilities.append("occupancy")
    if "system_mode" in states:
        capabilities.append("ac")

    if "system_mode" in states:
        hw_type = "thermostat"
    elif "occupancy" in states:
        hw_type = "occupancy_sensor"
    elif "color_temp_mireds" in states:
        hw_type = "color_temperature_light"
    elif "brightness_raw" in states:
        hw_type = "dimmable_light"
    elif "on_off" in states:
        hw_type = "on_off_light"
    else:
        return None

    return {
        "id": device["id"],
        "name": names[0] if names else device["id"],
        "names": names,
        "hardware_type": hw_type,
        "capabilities": capabilities,
        "states": states,
    }
