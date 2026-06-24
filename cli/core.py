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
    ATTR_HEATING_SETPOINT,
    ATTR_SYSTEM_MODE,
    SENSOR_KEYS,
    THERMO_COOL_MODES,
    THERMO_HEAT_MODES,
    THERMO_MODE_AUTO,
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

    def _route(self, resolved_id: str):
        """Resolve an id to (kind, device, client) — the single routing truth.

        Logical-first per the architecture rule: a command targeting an id that
        belongs to a remote logical bridge is forwarded there, not written to a
        same-id physical device. ``kind`` is "logical" | "physical" | None; for
        a logical device ``client`` may be None when its bridge is offline.
        """
        for dev in self.logical.get_all_devices().get("devices", []):
            if dev["id"] == resolved_id:
                return "logical", dev, self.logical.registry.get(dev["node_id"])
        phys = self._find_physical(resolved_id)
        if phys:
            return "physical", phys, None
        return None, None, None

    def _iter_devices(self):
        """Yield (device, names, origin) once per id, physical-first for identity.

        Dedup-by-id is applied here once, so every list endpoint and the status
        counts agree and a self-/mutual-federation loop can never double-count.
        """
        seen: set[str] = set()
        if self.bridge and self.bridge.cached_devices:
            for dev in self.bridge.cached_devices:
                if dev["id"] in seen:
                    continue
                seen.add(dev["id"])
                yield dev, self._resolved_names(dev), "physical"
        for dev in self.logical.get_all_devices().get("devices", []):
            if dev["id"] in seen:
                continue
            seen.add(dev["id"])
            yield dev, self._resolved_names(dev), "logical"

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
        local = self.bridge.names_for(dev["id"]) if self.bridge else []
        return serializers.resolved_names(dev, local)

    def _occupancy_ts(self, device_id: str):
        return self.bridge.occupancy_last_active(device_id) if self.bridge else None

    # -- Queries -------------------------------------------------------------

    def get_devices(self) -> list[dict]:
        result = []
        for dev, names, _origin in self._iter_devices():
            copy = dict(dev)
            copy["states"] = dict(dev.get("states", {}))
            if copy["states"].get("color_temp_mireds") == 0:
                copy["states"].pop("color_temp_mireds", None)
            copy["names"] = names
            result.append(copy)
        return result

    def get_lights(self) -> list[dict]:
        lights = []
        for dev, names, _origin in self._iter_devices():
            entry = serializers.build_light(dev, names)
            if entry:
                lights.append(entry)
        return lights

    def get_sensors(self) -> list[dict]:
        sensors = []
        for dev, names, _origin in self._iter_devices():
            entry = serializers.build_sensor(dev, names, self._occupancy_ts(dev["id"]))
            if entry:
                sensors.append(entry)
        return sensors

    def get_climate(self) -> list[dict]:
        out = []
        for dev, names, _origin in self._iter_devices():
            entry = serializers.build_climate(dev, names)
            if entry:
                out.append(entry)
        return out

    def get_climate_one(self, device_id: str) -> dict:
        resolved = self._resolve(device_id)
        _kind, dev, _client = self._route(resolved)
        if dev is None:
            raise KeyError(f"Device {resolved} not found")
        entry = serializers.build_climate(dev, self._resolved_names(dev))
        if entry:
            return entry
        raise ValueError(f"Device {resolved} has no climate data")

    def get_sensor(self, device_id: str) -> dict:
        resolved = self._resolve(device_id)
        _kind, dev, _client = self._route(resolved)
        if dev is None:
            raise KeyError("Sensor not found in cache")
        entry = serializers.build_sensor(
            dev, self._resolved_names(dev), self._occupancy_ts(resolved)
        )
        if entry:
            return entry
        raise ValueError("Device exists but contains no sensor clusters")

    def get_level(self, device_id: str) -> dict:
        resolved = self._resolve(device_id)
        _kind, dev, _client = self._route(resolved)
        if dev is not None and "brightness_raw" in dev.get("states", {}):
            return {"id": resolved, "level": dev["states"]["brightness_raw"]}
        raise KeyError("Device not found or level state unsupported")

    def get_mired(self, device_id: str) -> dict:
        resolved = self._resolve(device_id)
        _kind, dev, _client = self._route(resolved)
        if dev is not None and "color_temp_mireds" in dev.get("states", {}):
            return {"id": resolved, "mireds": dev["states"]["color_temp_mireds"]}
        raise KeyError("Device not found or color temperature unsupported")

    def get_status(self) -> dict:
        """Quick summary of all device states. Deduplicates federation loops by id."""
        lights_on = 0
        lights_off = 0
        sensors_active = 0
        acs_on = 0
        acs_off = 0
        total = 0
        devices_online = 0
        devices_offline = 0

        for dev, _names, _origin in self._iter_devices():
            total += 1
            if dev.get("online", True):
                devices_online += 1
            else:
                devices_offline += 1
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
            "total_devices": total,
            "devices_online": devices_online,
            "devices_offline": devices_offline,
        }

    # -- Control -------------------------------------------------------------

    async def set_device(
        self,
        device_id: str,
        brightness: Optional[float] = None,
        temperature: Optional[int] = None,
    ) -> dict:
        resolved = self._resolve(device_id)
        kind, dev, client = self._route(resolved)

        # AC (Thermostat) — only on/off via brightness. Setpoint/mode go via set_ac.
        # Ignore `temperature` here since it means Kelvin (color), not °C.
        # Logical-first via _route, so a same-id physical AC can't shadow a
        # logical one (the old code was physical-first here only).
        if dev is not None and self._is_ac(dev.get("states", {})):
            if brightness is None:
                return {"status": "noop", "id": resolved, "type": "ac"}
            return await self.set_ac(resolved, on=(brightness > 0))

        # Logical device
        if kind == "logical":
            if not client:
                raise RuntimeError("Logical bridge client offline")
            if brightness is not None:
                await asyncio.to_thread(
                    client.set_brightness, dev["id"], conv.clamp(brightness, 0.0, 1.0)
                )
            if temperature is not None and temperature > 0:
                mireds = conv.kelvin_to_mireds(temperature)
                await asyncio.to_thread(client.set_mired, dev["id"], mireds)
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
                transitionTime=0,
                optionsMask=0,
                optionsOverride=0,
            )
            await self.bridge.client.send_device_command(node_id, endpoint_id, cmd)

        return {"status": "success", "id": resolved, "type": "physical"}

    async def toggle(self, device_id: str) -> dict:
        resolved = self._resolve(device_id)
        _kind, dev, _client = self._route(resolved)
        if dev is None:
            raise KeyError(f"Device {resolved} not found")
        states = dev.get("states", {})

        # AC: SystemMode == 0 means off, anything else means on
        if self._is_ac(states):
            return await self.set_ac(resolved, on=(states.get("system_mode") == 0))

        if states.get("on_off"):
            return await self.set_device(resolved, brightness=0.0)
        else:
            return await self.set_device(resolved, brightness=1.0)

    async def set_level(self, device_id: str, level: int) -> dict:
        resolved = self._resolve(device_id)
        level = int(conv.clamp(level, 0, 254))

        kind, dev, client = self._route(resolved)
        if kind == "logical":
            if not client:
                raise RuntimeError("Logical bridge client offline")
            await asyncio.to_thread(client.set_level, dev["id"], level)
            return {
                "status": "success",
                "id": resolved,
                "level": level,
                "type": "logical",
            }

        self._verify_hardware()
        node_id, endpoint_id = self._parse_id(resolved)

        if level == 0:
            cmd = Clusters.OnOff.Commands.Off()
        else:
            cmd = Clusters.LevelControl.Commands.MoveToLevelWithOnOff(
                level=level, transitionTime=0
            )
        await self.bridge.client.send_device_command(node_id, endpoint_id, cmd)
        return {"status": "success", "id": resolved, "level": level, "type": "physical"}

    async def set_mired(self, device_id: str, mireds: int) -> dict:
        resolved = self._resolve(device_id)
        mireds = conv.clamp_mireds(mireds)

        kind, dev, client = self._route(resolved)
        if kind == "logical":
            if not client:
                raise RuntimeError("Logical bridge client offline")
            await asyncio.to_thread(client.set_mired, dev["id"], mireds)
            return {
                "status": "success",
                "id": resolved,
                "mireds": mireds,
                "type": "logical",
            }

        self._verify_hardware()
        node_id, endpoint_id = self._parse_id(resolved)

        cmd = Clusters.ColorControl.Commands.MoveToColorTemperature(
            colorTemperatureMireds=mireds,
            transitionTime=0,
            optionsMask=0,
            optionsOverride=0,
        )
        await self.bridge.client.send_device_command(node_id, endpoint_id, cmd)
        return {
            "status": "success",
            "id": resolved,
            "mireds": mireds,
            "type": "physical",
        }

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
        names = self.bridge.add_alias(resolved, name)
        return {"status": "success", "id": resolved, "names": names}

    def remove_name(self, device_id: str, name: str) -> dict:
        resolved = self._resolve(device_id)
        names = self.bridge.remove_alias(resolved, name)
        return {"status": "success", "id": resolved, "names": names}

    # -- Air conditioners (Thermostat-cluster devices) -----------------------

    def get_acs(self) -> list[dict]:
        out = []
        for dev, names, _origin in self._iter_devices():
            if self._is_ac(dev.get("states", {})):
                out.append(serializers.build_ac(dev, names))
        return out

    def get_ac(self, device_id: str) -> dict:
        resolved = self._resolve(device_id)
        _kind, dev, _client = self._route(resolved)
        if dev is not None and self._is_ac(dev.get("states", {})):
            return serializers.build_ac(dev, self._resolved_names(dev))
        raise KeyError(f"Device {resolved} is not an AC")

    async def set_ac(
        self,
        device_id: str,
        on: Optional[bool] = None,
        mode: Optional[int] = None,
        setpoint: Optional[float] = None,
        fan_speed: Optional[int] = None,
    ) -> dict:
        """Control an AC. on/off via SystemMode; setpoint in °C (e.g. 26.0).

        - on=True alone selects last-known non-zero mode, defaulting to Cool.
        - explicit mode overrides on; mode=0 is OFF.
        - fan_speed (0-100) only forwarded to logical-bridge devices that support it.
        """
        resolved = self._resolve(device_id)
        kind, dev, client = self._route(resolved)

        # Logical-first: if the device belongs to a remote logical bridge,
        # forward via REST instead of writing Matter clusters directly.
        if kind == "logical" and self._is_ac(dev.get("states", {})):
            if not client:
                raise RuntimeError("Logical bridge client offline")
            if mode is not None and int(mode) not in THERMO_VALID_MODES:
                raise ValueError(
                    f"Invalid SystemMode {mode}; valid: {sorted(THERMO_VALID_MODES)}"
                )
            await asyncio.to_thread(
                client.set_ac,
                resolved,
                on=on,
                mode=mode,
                setpoint=setpoint,
                fan_speed=fan_speed,
            )
            try:
                await asyncio.to_thread(client.refresh)
            except Exception:
                pass
            wrote = {}
            if mode is not None:
                wrote["system_mode"] = int(mode)
            elif on is False:
                wrote["system_mode"] = THERMO_MODE_OFF
            elif on is True:
                # Mirror the physical branch: report the mode we asked the
                # remote to resume (last non-zero, default Cool) — C4.
                cur = dev["states"].get("system_mode") or 0
                wrote["system_mode"] = cur if cur != 0 else THERMO_MODE_COOL
            if setpoint is not None:
                wrote["setpoint"] = conv.unit_to_centi(setpoint)  # neutral key (C3)
            if fan_speed is not None:
                wrote["fan_speed"] = int(fan_speed)
            return {
                "status": "success",
                "id": resolved,
                "wrote": wrote,
                "via": "logical",
            }

        # fan_speed is not a Matter Thermostat attribute — refuse rather than
        # silently dropping it on a mutation (API2).
        if fan_speed is not None:
            raise ValueError("fan_speed not supported on physical Matter ACs")

        self._verify_hardware()
        phys = dev if kind == "physical" else self._find_physical(resolved)
        if not phys or not self._is_ac(phys.get("states", {})):
            raise KeyError(f"Device {resolved} is not an AC")

        node_id, ep_id = phys["node_id"], phys["endpoint_id"]
        phys_wrote: dict = {}
        failed: dict = {}

        target_mode = None
        if mode is not None:
            if int(mode) not in THERMO_VALID_MODES:
                raise ValueError(
                    f"Invalid SystemMode {mode}; valid: {sorted(THERMO_VALID_MODES)}"
                )
            target_mode = int(mode)
        elif on is True:
            cur = phys["states"].get("system_mode") or 0
            target_mode = cur if cur != 0 else THERMO_MODE_COOL
        elif on is False:
            target_mode = THERMO_MODE_OFF

        # The setpoint attribute depends on the *effective* mode — the one being
        # set this call, else the device's current mode (C2). Writing the cooling
        # setpoint while in Heat used to silently no-op the active heating setpoint.
        effective_mode = (
            target_mode
            if target_mode is not None
            else phys["states"].get("system_mode")
        )

        async def _write(label: str, attr: int, value: int) -> None:
            try:
                await self.bridge.client.write_attribute(
                    node_id=node_id,
                    attribute_path=f"{ep_id}/{THERMOSTAT_CLUSTER}/{attr}",
                    value=value,
                )
                phys_wrote[label] = value
            except Exception as e:  # report which writes landed (E3)
                failed[label] = str(e)

        if target_mode is not None:
            await _write("system_mode", ATTR_SYSTEM_MODE, target_mode)

        if setpoint is not None:
            sp_centi = conv.unit_to_centi(setpoint)
            if effective_mode == THERMO_MODE_AUTO:
                # Auto has a two-point deadband; a single scalar is ambiguous.
                raise ValueError(
                    "Auto mode needs explicit cooling/heating setpoints; "
                    "a single setpoint is ambiguous"
                )
            elif effective_mode in THERMO_HEAT_MODES:
                await _write("setpoint", ATTR_HEATING_SETPOINT, sp_centi)
            elif effective_mode in THERMO_COOL_MODES:
                await _write("setpoint", ATTR_COOLING_SETPOINT, sp_centi)
            else:
                # Off / FanOnly / Dry / Sleep / unknown — default to cooling.
                await _write("setpoint", ATTR_COOLING_SETPOINT, sp_centi)

        self.bridge.sync()  # refresh cache even on a partial write
        result = {
            "status": "success" if not failed else "partial",
            "id": resolved,
            "wrote": phys_wrote,
        }
        if failed:
            result["failed"] = failed
        return result

    # -- Bridges -------------------------------------------------------------

    def add_bridge(self, ip: str, port: int, api_key: Optional[str] = None) -> dict:
        node_id = self.logical.add_bridge(ip, port, api_key=api_key)
        return {"status": "success", "message": f"Registered logical bridge {node_id}"}

    def remove_bridge(self, ip: str, port: int) -> dict:
        node_id = self.logical.remove_bridge(ip, port)
        return {"status": "success", "message": f"Removed logical bridge {node_id}"}

    async def register_device(
        self, code: str, ip: Optional[str] = None, name: Optional[str] = None
    ) -> dict:
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

        # Attach the alias to the exact device(s) of the newly-commissioned
        # node — not "first unnamed" (C1/A6). Surface name_not_applied (E7).
        assigned = None
        name_not_applied = False
        if name:
            self.bridge.sync()
            new_ids = (
                self.bridge.device_ids_for_node(new_node_id)
                if new_node_id is not None
                else []
            )
            for dev_id in new_ids:
                try:
                    self.set_name(dev_id, name)
                    assigned = dev_id
                    break
                except ValueError:
                    continue
            name_not_applied = assigned is None
        return {
            "status": "success",
            "code": code,
            "ip": ip,
            "node_id": new_node_id,
            "assigned_id": assigned,
            "name": name,
            "name_not_applied": name_not_applied,
            "deduped_nodes": deduped,
        }

    async def unregister_node(self, node_id: int) -> dict:
        """Unpair a fabric node by node_id. Use to clean up phantom entries."""
        self._verify_hardware()
        await self.bridge.client.send_command("remove_node", node_id=node_id)
        self.bridge.sync()
        self.bridge.prune_stale_occupancy()
        return {"status": "success", "removed_node_id": node_id}

    def refresh(self) -> dict:
        matter_status = "skipped"
        if self.bridge and self.bridge.is_ready():
            try:
                self.bridge.sync()
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
        for dev, names, _origin in self._iter_devices():
            if not dev.get("id"):
                continue
            entry = serializers.build_metadata(dev, names)
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
