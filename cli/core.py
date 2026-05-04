"""Shared business logic for Matter Web Controller.

Both the FastAPI server and MCP server import DeviceController from here.
"""

import asyncio
import datetime
import logging
from typing import Optional

import chip.clusters.Objects as Clusters

from cli.logic_bridge import LogicalBridgeManager
from cli.matter_bridge import MatterBridgeServer

# Thermostat SystemMode values used for AC control.
# 0=Off, 1=Auto, 3=Cool, 4=Heat, 5=EmergencyHeat, 6=Precooling, 7=FanOnly, 8=Dry, 9=Sleep
THERMO_MODE_OFF = 0
THERMO_MODE_COOL = 3
THERMO_MODE_HEAT = 4
THERMO_MODE_AUTO = 1
THERMO_VALID_MODES = {0, 1, 3, 4, 5, 6, 7, 8, 9}

SENSOR_KEYS = ["illuminance", "temperature", "pressure", "humidity", "occupancy", "contact"]
MIRED_MIN, MIRED_MAX = 153, 500  # Matter ColorControl spec range


def extract_matter_pin(setup_code: str) -> int:
    """Convert a Matter manual pairing code to a PIN."""
    clean = setup_code.replace("-", "").replace(" ", "")
    if len(clean) not in (11, 21) or not clean.isdigit():
        raise ValueError("Invalid manual pairing code format")
    return (int(clean[6:10]) << 14) | (int(clean[1:6]) & 0x3FFF)


class DeviceController:
    """Core device operations shared by HTTP and MCP interfaces."""

    def __init__(self, bridge: MatterBridgeServer, logical: LogicalBridgeManager):
        self.bridge = bridge
        self.logical = logical

    # -- Private helpers -----------------------------------------------------

    def _resolve(self, device_id: str) -> str:
        return device_id

    def _find_physical(self, resolved_id: str) -> dict | None:
        for dev in self.bridge.cached_devices:
            if dev["id"] == resolved_id:
                return dev
        return None

    def _find_logical(self, resolved_id: str):
        """Return (device_dict, client) or (None, None)."""
        for dev in self.logical.get_all_devices().get("devices", []):
            if dev["id"] == resolved_id:
                return dev, self.logical.registry.get(dev["node_id"])
        return None, None

    def _find_state(self, resolved_id: str, key: str):
        # Logical-first per architecture rule
        for dev in self.logical.get_all_devices().get("devices", []):
            if dev["id"] == resolved_id and key in dev.get("states", {}):
                return dev["states"][key]
        phys = self._find_physical(resolved_id)
        if phys and key in phys.get("states", {}):
            return phys["states"][key]
        return None

    @staticmethod
    def _is_ac(states: dict) -> bool:
        return "system_mode" in states

    def _parse_id(self, resolved_id: str) -> tuple[int, int]:
        phys = self._find_physical(resolved_id)
        if phys:
            return phys["node_id"], phys["endpoint_id"]
        raise KeyError(f"Physical device {resolved_id} not found in cache")

    def _verify_hardware(self):
        if not self.bridge or not self.bridge.is_ready():
            raise RuntimeError("Server not ready for hardware control")

    def _names_for(self, device_id: str) -> list:
        return self.bridge.device_names.get(device_id, [])

    def _all_devices_raw(self) -> list[dict]:
        result = []
        if self.bridge and self.bridge.cached_devices:
            result.extend(self.bridge.cached_devices)
        result.extend(self.logical.get_all_devices().get("devices", []))
        return result

    @staticmethod
    def _build_light(device: dict, names: list) -> dict | None:
        states = device.get("states", {})
        if "on_off" not in states and "brightness_raw" not in states:
            return None

        on = states.get("on_off")
        brightness = None
        if states.get("brightness_raw") is not None:
            brightness = round(max(0.0, min(1.0, states["brightness_raw"] / 254.0)), 2)
            if not on:
                brightness = 0.0

        entry = {"id": device["id"], "names": names, "state": on, "brightness": brightness}
        mireds = states.get("color_temp_mireds")
        if mireds and mireds > 0:
            entry["temperature"] = int(1_000_000 / mireds)
        return entry

    def _build_sensor(self, device: dict, names: list) -> dict | None:
        states = device.get("states", {})
        data = {k: states[k] for k in SENSOR_KEYS if k in states}
        if not data:
            return None

        if "occupancy" in data:
            ts = self.bridge.occupancy_history.get(device["id"]) if self.bridge else None
            if ts:
                data["occupancy_last_active"] = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

        return {"id": device["id"], "names": names, **data}

    # -- Queries -------------------------------------------------------------

    def get_devices(self) -> list[dict]:
        result = []
        if self.bridge and self.bridge.cached_devices:
            for dev in self.bridge.cached_devices:
                copy = dict(dev)
                copy["states"] = dict(dev.get("states", {}))
                if copy["states"].get("color_temp_mireds") == 0:
                    copy["states"].pop("color_temp_mireds", None)
                copy["names"] = self._names_for(dev["id"])
                result.append(copy)
        result.extend(self.logical.get_all_devices().get("devices", []))
        return result

    def get_lights(self) -> list[dict]:
        lights = []
        if self.bridge:
            for dev in self.bridge.cached_devices:
                entry = self._build_light(dev, self._names_for(dev["id"]))
                if entry:
                    lights.append(entry)
        for dev in self.logical.get_all_devices().get("devices", []):
            entry = self._build_light(dev, dev.get("names", []))
            if entry:
                lights.append(entry)
        return lights

    def get_sensors(self) -> list[dict]:
        if not self.bridge:
            return []
        sensors = []
        for dev in self.bridge.cached_devices:
            entry = self._build_sensor(dev, self._names_for(dev["id"]))
            if entry:
                sensors.append(entry)
        return sensors

    @staticmethod
    def _climate_entry(dev: dict, names: list) -> dict | None:
        states = dev.get("states", {})
        temp_c = None
        humidity = None
        kind = None

        if "local_temperature" in states:
            temp_c = round(states["local_temperature"] / 100.0, 2)
            kind = "thermostat"
        elif "temperature" in states:
            temp_c = round(states["temperature"] / 100.0, 2)
            kind = "sensor"

        if "humidity" in states:
            humidity = round(states["humidity"] / 100.0, 2)
            kind = kind or "sensor"

        if temp_c is None and humidity is None:
            return None

        out = {"id": dev["id"], "names": names, "kind": kind}
        if temp_c is not None:
            out["temperature"] = temp_c
        if humidity is not None:
            out["humidity"] = humidity
        return out

    def get_climate(self) -> list[dict]:
        out = []
        if self.bridge:
            for dev in self.bridge.cached_devices:
                entry = self._climate_entry(dev, self._names_for(dev["id"]))
                if entry:
                    out.append(entry)
        for dev in self.logical.get_all_devices().get("devices", []):
            entry = self._climate_entry(dev, dev.get("names", []))
            if entry:
                out.append(entry)
        return out

    def get_climate_one(self, device_id: str) -> dict:
        resolved = self._resolve(device_id)
        for dev in self._all_devices_raw():
            if dev.get("id") != resolved:
                continue
            names = self._names_for(resolved) or dev.get("names", [])
            entry = self._climate_entry(dev, names)
            if entry:
                return entry
            raise ValueError(f"Device {resolved} has no climate data")
        raise KeyError(f"Device {resolved} not found")

    def get_sensor(self, device_id: str) -> dict:
        resolved = self._resolve(device_id)
        # Logical-first
        for dev in self.logical.get_all_devices().get("devices", []):
            if dev["id"] == resolved:
                entry = self._build_sensor(dev, dev.get("names", []))
                if entry:
                    return entry
                raise ValueError("Device exists but contains no sensor clusters")
        for dev in self.bridge.cached_devices:
            if dev["id"] != resolved:
                continue
            entry = self._build_sensor(dev, self._names_for(resolved))
            if entry:
                return entry
            raise ValueError("Device exists but contains no sensor clusters")
        raise KeyError("Sensor not found in cache")

    def get_level(self, device_id: str) -> dict:
        resolved = self._resolve(device_id)
        val = self._find_state(resolved, "brightness_raw")
        if val is not None:
            return {"id": resolved, "level": val}
        raise KeyError("Device not found or level state unsupported")

    def get_mired(self, device_id: str) -> dict:
        resolved = self._resolve(device_id)
        val = self._find_state(resolved, "color_temp_mireds")
        if val is not None:
            return {"id": resolved, "mireds": val}
        raise KeyError("Device not found or color temperature unsupported")

    def get_status(self) -> dict:
        """Quick summary of all device states. Deduplicates federation loops by id."""
        lights_on = 0
        lights_off = 0
        sensors_active = 0
        acs_on = 0
        acs_off = 0
        seen: set[str] = set()

        for dev in self._all_devices_raw():
            dev_id = dev.get("id")
            if not dev_id or dev_id in seen:
                continue
            seen.add(dev_id)
            states = dev.get("states", {})
            if "on_off" in states or "brightness_raw" in states:
                if states.get("on_off"):
                    lights_on += 1
                else:
                    lights_off += 1
            if any(k in states for k in SENSOR_KEYS):
                sensors_active += 1
            if self._is_ac(states):
                if states.get("system_mode"):
                    acs_on += 1
                else:
                    acs_off += 1

        return {
            "lights_on": lights_on,
            "lights_off": lights_off,
            "sensors_active": sensors_active,
            "acs_on": acs_on,
            "acs_off": acs_off,
            "logical_bridges": len(self.logical.registry),
            "total_devices": len(seen),
        }

    # -- Control -------------------------------------------------------------

    async def set_device(self, device_id: str,
                         brightness: Optional[float] = None,
                         temperature: Optional[int] = None) -> dict:
        resolved = self._resolve(device_id)

        # AC (Thermostat) — only on/off via brightness. Setpoint/mode go via set_ac.
        # Ignore `temperature` here since it means Kelvin (color), not °C.
        phys = self._find_physical(resolved)
        if phys and self._is_ac(phys.get("states", {})):
            if brightness is None:
                return {"status": "noop", "id": resolved, "type": "ac"}
            return await self.set_ac(resolved, on=(brightness > 0))

        # Logical device
        target, client = self._find_logical(resolved)
        if target:
            if not client:
                raise RuntimeError("Logical bridge client offline")
            if brightness is not None:
                client.set_brightness(target["id"], max(0.0, min(1.0, brightness)))
            if temperature is not None and temperature > 0:
                mireds = max(MIRED_MIN, min(MIRED_MAX, int(1_000_000 / temperature)))
                client.set_mired(target["id"], mireds)
            return {"status": "success", "id": resolved, "type": "logical"}

        # Physical device
        self._verify_hardware()
        node_id, endpoint_id = self._parse_id(resolved)

        if brightness is not None:
            brightness = max(0.0, min(1.0, brightness))
            if brightness == 0.0:
                cmd = Clusters.OnOff.Commands.Off()
            else:
                cmd = Clusters.LevelControl.Commands.MoveToLevelWithOnOff(
                    level=max(1, int(brightness * 254)), transitionTime=0
                )
            await self.bridge.client.send_device_command(node_id, endpoint_id, cmd)

        if temperature is not None and temperature > 0:
            mireds = max(MIRED_MIN, min(MIRED_MAX, int(1_000_000 / temperature)))
            cmd = Clusters.ColorControl.Commands.MoveToColorTemperature(
                colorTemperatureMireds=mireds,
                transitionTime=0, optionsMask=0, optionsOverride=0,
            )
            await self.bridge.client.send_device_command(node_id, endpoint_id, cmd)

        return {"status": "success", "id": resolved, "type": "physical"}

    async def toggle(self, device_id: str) -> dict:
        resolved = self._resolve(device_id)

        # AC: SystemMode == 0 means off, anything else means on
        sm = self._find_state(resolved, "system_mode")
        if sm is not None:
            return await self.set_ac(resolved, on=(sm == 0))

        is_on = self._find_state(resolved, "on_off")
        if is_on:
            return await self.set_device(resolved, brightness=0.0)
        else:
            return await self.set_device(resolved, brightness=1.0)

    async def set_level(self, device_id: str, level: int) -> dict:
        resolved = self._resolve(device_id)
        level = max(0, min(254, level))

        target, client = self._find_logical(resolved)
        if target:
            if not client:
                raise RuntimeError("Logical bridge client offline")
            client.set_level(target["id"], level)
            return {"status": "success", "id": resolved, "level": level, "type": "logical"}

        self._verify_hardware()
        node_id, endpoint_id = self._parse_id(resolved)

        if level == 0:
            cmd = Clusters.OnOff.Commands.Off()
        else:
            cmd = Clusters.LevelControl.Commands.MoveToLevelWithOnOff(level=level, transitionTime=0)
        await self.bridge.client.send_device_command(node_id, endpoint_id, cmd)
        return {"status": "success", "id": resolved, "level": level, "type": "physical"}

    async def set_mired(self, device_id: str, mireds: int) -> dict:
        resolved = self._resolve(device_id)
        mireds = max(MIRED_MIN, min(MIRED_MAX, int(mireds)))

        target, client = self._find_logical(resolved)
        if target:
            if not client:
                raise RuntimeError("Logical bridge client offline")
            client.set_mired(target["id"], mireds)
            return {"status": "success", "id": resolved, "mireds": mireds, "type": "logical"}

        self._verify_hardware()
        node_id, endpoint_id = self._parse_id(resolved)

        cmd = Clusters.ColorControl.Commands.MoveToColorTemperature(
            colorTemperatureMireds=mireds, transitionTime=0, optionsMask=0, optionsOverride=0,
        )
        await self.bridge.client.send_device_command(node_id, endpoint_id, cmd)
        return {"status": "success", "id": resolved, "mireds": mireds, "type": "physical"}

    async def batch_control(self, actions: list[dict]) -> list[dict]:
        async def run(action: dict) -> dict:
            try:
                return await self.set_device(
                    action["id"],
                    brightness=action.get("brightness"),
                    temperature=action.get("temperature"),
                )
            except Exception as e:
                return {"status": "error", "id": action.get("id"), "detail": str(e)}

        return await asyncio.gather(*(run(a) for a in actions))

    # -- Management ----------------------------------------------------------

    def set_name(self, device_id: str, name: str) -> dict:
        resolved = self._resolve(device_id)

        for existing_id, names in self.bridge.device_names.items():
            if name in names and existing_id != resolved:
                raise ValueError("Name conflict: Alias already assigned to another device")

        self.bridge.device_names.setdefault(resolved, [])
        if name not in self.bridge.device_names[resolved]:
            self.bridge.device_names[resolved].append(name)
            self.bridge._save_names_cache()

        return {"status": "success", "id": resolved, "names": self.bridge.device_names[resolved]}

    def remove_name(self, device_id: str, name: str) -> dict:
        resolved = self._resolve(device_id)

        names = self.bridge.device_names.get(resolved, [])
        if name not in names:
            raise KeyError(f"Alias '{name}' not found on device {resolved}")

        names.remove(name)
        if not names:
            del self.bridge.device_names[resolved]
        self.bridge._save_names_cache()

        return {"status": "success", "id": resolved, "names": self.bridge.device_names.get(resolved, [])}

    # -- Air conditioners (Thermostat-cluster devices) -----------------------

    @staticmethod
    def _ac_entry(dev: dict, names: list) -> dict:
        s = dev.get("states", {})
        out = {
            "id": dev["id"],
            "names": names,
            "system_mode": s.get("system_mode"),
            "on": bool(s.get("system_mode")),  # 0=Off → False; any non-zero → True
        }
        if "local_temperature" in s:
            out["local_temperature"] = round(s["local_temperature"] / 100.0, 2)
        if "cooling_setpoint" in s:
            out["cooling_setpoint"] = round(s["cooling_setpoint"] / 100.0, 2)
        if "heating_setpoint" in s:
            out["heating_setpoint"] = round(s["heating_setpoint"] / 100.0, 2)
        return out

    def get_acs(self) -> list[dict]:
        if not self.bridge:
            return []
        return [
            self._ac_entry(dev, self._names_for(dev["id"]))
            for dev in self.bridge.cached_devices
            if self._is_ac(dev.get("states", {}))
        ]

    def get_ac(self, device_id: str) -> dict:
        resolved = self._resolve(device_id)
        phys = self._find_physical(resolved)
        if not phys or not self._is_ac(phys.get("states", {})):
            raise KeyError(f"Device {resolved} is not an AC")
        return self._ac_entry(phys, self._names_for(resolved))

    async def set_ac(self, device_id: str,
                     on: Optional[bool] = None,
                     mode: Optional[int] = None,
                     setpoint: Optional[float] = None) -> dict:
        """Control an AC. on/off via SystemMode; setpoint in °C (e.g. 26.0).

        - on=True alone selects last-known non-zero mode, defaulting to Cool.
        - explicit mode overrides on; mode=0 is OFF.
        """
        resolved = self._resolve(device_id)
        self._verify_hardware()
        phys = self._find_physical(resolved)
        if not phys or not self._is_ac(phys.get("states", {})):
            raise KeyError(f"Device {resolved} is not an AC")

        node_id, ep_id = phys["node_id"], phys["endpoint_id"]
        wrote = []

        target_mode = None
        if mode is not None:
            if int(mode) not in THERMO_VALID_MODES:
                raise ValueError(f"Invalid SystemMode {mode}; valid: {sorted(THERMO_VALID_MODES)}")
            target_mode = int(mode)
        elif on is True:
            cur = phys["states"].get("system_mode") or 0
            target_mode = cur if cur != 0 else THERMO_MODE_COOL
        elif on is False:
            target_mode = THERMO_MODE_OFF

        if target_mode is not None:
            await self.bridge.client.write_attribute(
                node_id=node_id,
                attribute_path=f"{ep_id}/513/28",
                value=target_mode,
            )
            wrote.append(("system_mode", target_mode))

        if setpoint is not None:
            sp_centi = int(round(float(setpoint) * 100))
            await self.bridge.client.write_attribute(
                node_id=node_id,
                attribute_path=f"{ep_id}/513/17",
                value=sp_centi,
            )
            wrote.append(("cooling_setpoint", sp_centi))

        self.bridge._update_cache()
        return {"status": "success", "id": resolved, "wrote": dict(wrote)}

    # -- Bridges -------------------------------------------------------------

    def add_bridge(self, ip: str, port: int, api_key: Optional[str] = None) -> dict:
        node_id = self.logical.add_bridge(ip, port, api_key=api_key)
        return {"status": "success", "message": f"Registered logical bridge {node_id}"}

    def remove_bridge(self, ip: str, port: int) -> dict:
        node_id = self.logical.remove_bridge(ip, port)
        return {"status": "success", "message": f"Removed logical bridge {node_id}"}

    async def register_device(self, code: str, ip: Optional[str] = None, name: Optional[str] = None) -> dict:
        self._verify_hardware()

        if ip:
            pin = extract_matter_pin(code)
            result = await self.bridge.client.send_command(
                "commission_on_network", setup_pin_code=pin, ip_address=ip
            )
        else:
            # network_only=True: discover via mDNS on LAN, no BLE required.
            result = await self.bridge.client.send_command(
                "commission_with_code", code=code, network_only=True
            )

        new_node_id = None
        if isinstance(result, dict):
            new_node_id = result.get("node_id")
        elif hasattr(result, "node_id"):
            new_node_id = result.node_id

        deduped: list[int] = []
        if new_node_id is not None:
            deduped = await self.bridge.dedupe_by_unique_id(new_node_id)

        # Persist alias to whichever new device showed up
        assigned = None
        if name:
            self.bridge._update_cache()
            existing_ids = {d["id"] for d in self.bridge.cached_devices}
            # Pick the device that appeared after commission (heuristic: not already named)
            for dev in self.bridge.cached_devices:
                if dev["id"] in existing_ids and dev["id"] not in self.bridge.device_names:
                    try:
                        self.set_name(dev["id"], name)
                        assigned = dev["id"]
                        break
                    except ValueError:
                        continue
        return {
            "status": "success",
            "code": code,
            "ip": ip,
            "node_id": new_node_id,
            "assigned_id": assigned,
            "name": name,
            "deduped_nodes": deduped,
        }

    async def unregister_node(self, node_id: int) -> dict:
        """Unpair a fabric node by node_id. Use to clean up phantom entries."""
        self._verify_hardware()
        await self.bridge.client.send_command("remove_node", node_id=node_id)
        self.bridge._update_cache()
        return {"status": "success", "removed_node_id": node_id}

    def refresh(self) -> dict:
        matter_status = "skipped"
        if self.bridge and self.bridge.is_ready():
            try:
                self.bridge._update_cache()
                matter_status = "success"
            except Exception as e:
                logging.error(f"Matter bridge refresh error: {e}")
                matter_status = "failed"

        count = self.logical.refresh_bridges()
        return {"status": "success", "message": f"Refreshed {count} logical bridges. Matter: {matter_status}"}

    def get_metadata(self, host: str, port: int) -> dict:
        """Declarative bridge metadata for federation discovery.

        Federation peers consume the device list via /api/devices and call
        /api/level, /api/mired, /api/set directly — this endpoint is purely
        informational (capabilities + current states).
        """
        metadata = []
        for dev in self._all_devices_raw():
            dev_id = dev.get("id")
            if not dev_id:
                continue

            names = list(dev.get("names", []))
            if self.bridge:
                for n in self.bridge.device_names.get(dev_id, []):
                    if n not in names:
                        names.append(n)

            states = dev.get("states", {})
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
                continue

            metadata.append({
                "id": dev_id,
                "name": names[0] if names else dev_id,
                "names": names,
                "hardware_type": hw_type,
                "capabilities": capabilities,
                "states": states,
            })

        return {
            "bridge": {
                "id": "matter_bridge_http",
                "type": "lighting_controller",
                "network_host": host,
                "network_port": port,
                "api_version": "2",
            },
            "devices": metadata,
        }
