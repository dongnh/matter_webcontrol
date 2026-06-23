"""Shared business logic for Matter Web Controller.

Both the FastAPI server and MCP server import DeviceController from here.
"""

import asyncio
import logging
from typing import Optional

import chip.clusters.Objects as Clusters

from cli import conversions as conv
from cli import serializers
from cli.constants import (
    ATTR_COOLING_SETPOINT,
    ATTR_SYSTEM_MODE,
    SENSOR_KEYS,
    THERMO_MODE_COOL,
    THERMO_MODE_OFF,
    THERMO_VALID_MODES,
    THERMOSTAT_CLUSTER,
    extract_matter_pin,
)
from cli.logic_bridge import LogicalBridgeManager
from cli.matter_bridge import MatterBridgeServer

__all__ = ["DeviceController", "extract_matter_pin"]


class DeviceController:
    """Core device operations shared by HTTP and MCP interfaces."""

    def __init__(self, bridge: MatterBridgeServer, logical: LogicalBridgeManager):
        self.bridge = bridge
        self.logical = logical

    # -- Private helpers -----------------------------------------------------

    def _resolve(self, device_id: str) -> str:
        # Identity by design: device IDs are canonical; aliases are display-only
        # and are never resolved as IDs.
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

    def _resolved_names(self, dev: dict) -> list:
        """Merge locally-assigned aliases with any names on a remote device."""
        local = self.bridge.device_names.get(dev["id"], []) if self.bridge else []
        return serializers.resolved_names(dev, local)

    def _occupancy_ts(self, device_id: str):
        return self.bridge.occupancy_history.get(device_id) if self.bridge else None

    def _all_devices_raw(self) -> list[dict]:
        result = []
        if self.bridge and self.bridge.cached_devices:
            result.extend(self.bridge.cached_devices)
        result.extend(self.logical.get_all_devices().get("devices", []))
        return result

    # -- Queries -------------------------------------------------------------

    def get_devices(self) -> list[dict]:
        result = []
        if self.bridge and self.bridge.cached_devices:
            for dev in self.bridge.cached_devices:
                copy = dict(dev)
                copy["states"] = dict(dev.get("states", {}))
                if copy["states"].get("color_temp_mireds") == 0:
                    copy["states"].pop("color_temp_mireds", None)
                copy["names"] = self._resolved_names(dev)
                result.append(copy)
        for dev in self.logical.get_all_devices().get("devices", []):
            copy = dict(dev)
            copy["names"] = self._resolved_names(dev)
            result.append(copy)
        return result

    def get_lights(self) -> list[dict]:
        lights = []
        if self.bridge:
            for dev in self.bridge.cached_devices:
                entry = serializers.build_light(dev, self._resolved_names(dev))
                if entry:
                    lights.append(entry)
        for dev in self.logical.get_all_devices().get("devices", []):
            entry = serializers.build_light(dev, self._resolved_names(dev))
            if entry:
                lights.append(entry)
        return lights

    def get_sensors(self) -> list[dict]:
        if not self.bridge:
            return []
        sensors = []
        for dev in self.bridge.cached_devices:
            entry = serializers.build_sensor(
                dev, self._resolved_names(dev), self._occupancy_ts(dev["id"])
            )
            if entry:
                sensors.append(entry)
        return sensors

    def get_climate(self) -> list[dict]:
        out = []
        if self.bridge:
            for dev in self.bridge.cached_devices:
                entry = serializers.build_climate(dev, self._resolved_names(dev))
                if entry:
                    out.append(entry)
        for dev in self.logical.get_all_devices().get("devices", []):
            entry = serializers.build_climate(dev, self._resolved_names(dev))
            if entry:
                out.append(entry)
        return out

    def get_climate_one(self, device_id: str) -> dict:
        resolved = self._resolve(device_id)
        for dev in self._all_devices_raw():
            if dev.get("id") != resolved:
                continue
            entry = serializers.build_climate(dev, self._resolved_names(dev))
            if entry:
                return entry
            raise ValueError(f"Device {resolved} has no climate data")
        raise KeyError(f"Device {resolved} not found")

    def get_sensor(self, device_id: str) -> dict:
        resolved = self._resolve(device_id)
        # Logical-first
        for dev in self.logical.get_all_devices().get("devices", []):
            if dev["id"] == resolved:
                entry = serializers.build_sensor(
                    dev, self._resolved_names(dev), self._occupancy_ts(resolved)
                )
                if entry:
                    return entry
                raise ValueError("Device exists but contains no sensor clusters")
        for dev in self.bridge.cached_devices:
            if dev["id"] != resolved:
                continue
            entry = serializers.build_sensor(
                dev, self._resolved_names(dev), self._occupancy_ts(resolved)
            )
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
                await asyncio.to_thread(
                    client.set_brightness, target["id"], conv.clamp(brightness, 0.0, 1.0)
                )
            if temperature is not None and temperature > 0:
                mireds = conv.kelvin_to_mireds(temperature)
                await asyncio.to_thread(client.set_mired, target["id"], mireds)
            return {"status": "success", "id": resolved, "type": "logical"}

        # Physical device
        self._verify_hardware()
        node_id, endpoint_id = self._parse_id(resolved)

        if brightness is not None:
            brightness = conv.clamp(brightness, 0.0, 1.0)
            if brightness == 0.0:
                cmd = Clusters.OnOff.Commands.Off()
            else:
                cmd = Clusters.LevelControl.Commands.MoveToLevelWithOnOff(
                    level=conv.denormalize_brightness(brightness), transitionTime=0
                )
            await self.bridge.client.send_device_command(node_id, endpoint_id, cmd)

        if temperature is not None and temperature > 0:
            mireds = conv.kelvin_to_mireds(temperature)
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
        level = int(conv.clamp(level, 0, 254))

        target, client = self._find_logical(resolved)
        if target:
            if not client:
                raise RuntimeError("Logical bridge client offline")
            await asyncio.to_thread(client.set_level, target["id"], level)
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
        mireds = conv.clamp_mireds(mireds)

        target, client = self._find_logical(resolved)
        if target:
            if not client:
                raise RuntimeError("Logical bridge client offline")
            await asyncio.to_thread(client.set_mired, target["id"], mireds)
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

    def get_acs(self) -> list[dict]:
        out = []
        if self.bridge:
            out.extend(
                serializers.build_ac(dev, self._resolved_names(dev))
                for dev in self.bridge.cached_devices
                if self._is_ac(dev.get("states", {}))
            )
        for dev in self.logical.get_all_devices().get("devices", []):
            if self._is_ac(dev.get("states", {})):
                out.append(serializers.build_ac(dev, self._resolved_names(dev)))
        return out

    def get_ac(self, device_id: str) -> dict:
        resolved = self._resolve(device_id)
        log_dev, _ = self._find_logical(resolved)
        if log_dev and self._is_ac(log_dev.get("states", {})):
            return serializers.build_ac(log_dev, self._resolved_names(log_dev))
        phys = self._find_physical(resolved)
        if not phys or not self._is_ac(phys.get("states", {})):
            raise KeyError(f"Device {resolved} is not an AC")
        return serializers.build_ac(phys, self._resolved_names(phys))

    async def set_ac(self, device_id: str,
                     on: Optional[bool] = None,
                     mode: Optional[int] = None,
                     setpoint: Optional[float] = None,
                     fan_speed: Optional[int] = None) -> dict:
        """Control an AC. on/off via SystemMode; setpoint in °C (e.g. 26.0).

        - on=True alone selects last-known non-zero mode, defaulting to Cool.
        - explicit mode overrides on; mode=0 is OFF.
        - fan_speed (0-100) only forwarded to logical-bridge devices that support it.
        """
        resolved = self._resolve(device_id)

        # Logical-first: if the device belongs to a remote logical bridge,
        # forward via REST instead of writing Matter clusters directly.
        log_dev, client = self._find_logical(resolved)
        if log_dev and client and self._is_ac(log_dev.get("states", {})):
            if mode is not None and int(mode) not in THERMO_VALID_MODES:
                raise ValueError(f"Invalid SystemMode {mode}; valid: {sorted(THERMO_VALID_MODES)}")
            await asyncio.to_thread(
                client.set_ac,
                resolved, on=on, mode=mode, setpoint=setpoint, fan_speed=fan_speed,
            )
            try:
                await asyncio.to_thread(client.refresh)
            except Exception:
                pass
            wrote = {}
            if mode is not None: wrote["system_mode"] = int(mode)
            elif on is False: wrote["system_mode"] = THERMO_MODE_OFF
            if setpoint is not None: wrote["heating_setpoint"] = conv.unit_to_centi(setpoint)
            if fan_speed is not None: wrote["fan_speed"] = int(fan_speed)
            return {"status": "success", "id": resolved, "wrote": wrote, "via": "logical"}

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
                attribute_path=f"{ep_id}/{THERMOSTAT_CLUSTER}/{ATTR_SYSTEM_MODE}",
                value=target_mode,
            )
            wrote.append(("system_mode", target_mode))

        if setpoint is not None:
            sp_centi = conv.unit_to_centi(setpoint)
            await self.bridge.client.write_attribute(
                node_id=node_id,
                attribute_path=f"{ep_id}/{THERMOSTAT_CLUSTER}/{ATTR_COOLING_SETPOINT}",
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

        result = self.logical.refresh_bridges()
        return {
            "status": "success",
            "message": (
                f"Refreshed {result['refreshed']} logical bridges "
                f"({result['failed']} failed). Matter: {matter_status}"
            ),
            "refreshed": result["refreshed"],
            "failed": result["failed"],
            "matter": matter_status,
        }

    def get_metadata(self, host: str, port: int) -> dict:
        """Declarative bridge metadata for federation discovery.

        Federation peers consume the device list via /api/devices and call
        /api/level, /api/mired, /api/set directly — this endpoint is purely
        informational (capabilities + current states).
        """
        metadata = []
        for dev in self._all_devices_raw():
            if not dev.get("id"):
                continue
            entry = serializers.build_metadata(dev, self._resolved_names(dev))
            if entry:
                metadata.append(entry)

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
