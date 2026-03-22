"""Shared business logic for Matter Web Controller.

Both the FastAPI server and MCP server import DeviceController from here.
"""

import datetime
import logging
from typing import Optional

import chip.clusters.Objects as Clusters

from cli.logic_bridge import LogicalBridgeManager
from cli.matter_bridge import MatterBridgeServer

SENSOR_KEYS = ["illuminance", "temperature", "pressure", "humidity", "occupancy", "contact"]


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
        phys = self._find_physical(resolved_id)
        if phys and key in phys.get("states", {}):
            return phys["states"][key]
        for dev in self.logical.get_all_devices().get("devices", []):
            if dev["id"] == resolved_id and key in dev.get("states", {}):
                return dev["states"][key]
        return None

    def _parse_id(self, resolved_id: str) -> tuple[int, int]:
        phys = self._find_physical(resolved_id)
        if phys:
            return phys["node_id"], phys["endpoint_id"]
        raise ValueError("Physical device not found in cache")

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

    def get_sensor(self, device_id: str) -> dict:
        resolved = self._resolve(device_id)
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
        """Quick summary of all device states."""
        lights_on = 0
        lights_off = 0
        sensors_active = 0

        for dev in self._all_devices_raw():
            states = dev.get("states", {})
            if "on_off" in states or "brightness_raw" in states:
                if states.get("on_off"):
                    lights_on += 1
                else:
                    lights_off += 1
            for k in SENSOR_KEYS:
                if k in states:
                    sensors_active += 1
                    break

        return {
            "lights_on": lights_on,
            "lights_off": lights_off,
            "sensors_active": sensors_active,
            "logical_bridges": len(self.logical.registry),
            "total_devices": lights_on + lights_off + sensors_active,
        }

    # -- Control -------------------------------------------------------------

    async def set_device(self, device_id: str,
                         brightness: Optional[float] = None,
                         temperature: Optional[int] = None) -> dict:
        resolved = self._resolve(device_id)

        # Logical device
        target, client = self._find_logical(resolved)
        if target:
            if not client:
                raise RuntimeError("Logical bridge client offline")
            if brightness is not None:
                level = int(max(0.0, min(1.0, brightness)) * 254)
                client.execute_event(target["endpoint_id"], "set_level", str(level))
            if temperature is not None and temperature > 0:
                client.execute_event(target["endpoint_id"], "set_color_temperature", str(int(1_000_000 / temperature)))
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
            cmd = Clusters.ColorControl.Commands.MoveToColorTemperature(
                colorTemperatureMireds=int(1_000_000 / temperature),
                transitionTime=0, optionsMask=0, optionsOverride=0,
            )
            await self.bridge.client.send_device_command(node_id, endpoint_id, cmd)

        return {"status": "success", "id": resolved, "type": "physical"}

    async def toggle(self, device_id: str) -> dict:
        resolved = self._resolve(device_id)
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
            client.execute_event(target["endpoint_id"], "set_level", str(level))
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

        target, client = self._find_logical(resolved)
        if target:
            if not client:
                raise RuntimeError("Logical bridge client offline")
            client.execute_event(target["endpoint_id"], "set_color_temperature", str(mireds))
            return {"status": "success", "id": resolved, "mireds": mireds, "type": "logical"}

        self._verify_hardware()
        node_id, endpoint_id = self._parse_id(resolved)

        cmd = Clusters.ColorControl.Commands.MoveToColorTemperature(
            colorTemperatureMireds=mireds, transitionTime=0, optionsMask=0, optionsOverride=0,
        )
        await self.bridge.client.send_device_command(node_id, endpoint_id, cmd)
        return {"status": "success", "id": resolved, "mireds": mireds, "type": "physical"}

    async def batch_control(self, actions: list[dict]) -> list[dict]:
        results = []
        for action in actions:
            try:
                r = await self.set_device(
                    action["id"],
                    brightness=action.get("brightness"),
                    temperature=action.get("temperature"),
                )
                results.append(r)
            except Exception as e:
                results.append({"status": "error", "id": action.get("id"), "detail": str(e)})
        return results

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

    def add_bridge(self, ip: str, port: int) -> dict:
        node_id = self.logical.add_bridge(ip, port)
        return {"status": "success", "message": f"Registered logical bridge {node_id}"}

    def remove_bridge(self, ip: str, port: int) -> dict:
        node_id = self.logical.remove_bridge(ip, port)
        return {"status": "success", "message": f"Removed logical bridge {node_id}"}

    async def register_device(self, code: str, ip: Optional[str] = None, name: Optional[str] = None) -> dict:
        self._verify_hardware()

        if ip:
            pin = extract_matter_pin(code)
            await self.bridge.client.send_command(
                "commission_on_network", setup_pin_code=pin, ip_address=ip
            )
        else:
            await self.bridge.client.send_command("commission_with_code", code=code)
        return {"status": "success", "code": code, "ip": ip, "pending_name": name}

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
        base = f"http://{host}:{port}"
        all_devs = self._all_devices_raw()

        metadata = []
        for dev in all_devs:
            dev_id = dev.get("id")
            if not dev_id:
                continue

            names = list(dev.get("names", []))
            if self.bridge:
                for n in self.bridge.device_names.get(dev_id, []):
                    if n not in names:
                        names.append(n)

            name = names[0] if names else dev_id
            states = dev.get("states", {})

            has_on_off = "on_off" in states
            has_brightness = "brightness_raw" in states
            has_color_temp = "color_temp_mireds" in states
            has_occupancy = "occupancy" in states

            events = {}
            hw_type = "unknown"

            if has_occupancy:
                hw_type = "occupancy_sensor"
                events["read_occupancy"] = {
                    "trigger": "occupancy_sensing_cluster",
                    "script": (
                        f"import urllib.request, json\n"
                        f"response = urllib.request.urlopen('{base}/api/sensor?id={dev_id}')\n"
                        f"data = json.loads(response.read().decode('utf-8'))\n"
                        f"print(data.get('occupancy', 0))"
                    ),
                }
                events["subscribe_occupancy"] = {
                    "trigger": "occupancy_sse_stream",
                    "script": (
                        f"import urllib.request\n"
                        f"response = urllib.request.urlopen('{base}/api/subscribe?id={dev_id}')\n"
                        f"for line in response:\n"
                        f"    print(line.decode('utf-8').strip())"
                    ),
                }
            elif has_on_off:
                if has_color_temp:
                    hw_type = "color_temperature_light"
                elif has_brightness:
                    hw_type = "dimmable_light"
                else:
                    hw_type = "on_off_light"

                events["turn_on"] = {
                    "trigger": "on_off_cluster",
                    "script": f"import urllib.request\nurllib.request.urlopen('{base}/api/set?id={dev_id}&brightness=1.0')",
                }
                events["turn_off"] = {
                    "trigger": "on_off_cluster",
                    "script": f"import urllib.request\nurllib.request.urlopen('{base}/api/set?id={dev_id}&brightness=0.0')",
                }

                if has_brightness or has_color_temp:
                    events["set_level"] = {
                        "trigger": "level_control_cluster",
                        "script": (
                            f"import sys, urllib.request\n"
                            f"level = int(sys.argv[1]) if len(sys.argv) > 1 else 254\n"
                            f"urllib.request.urlopen(f'{base}/api/level?id={dev_id}&level={{level}}')"
                        ),
                    }
                    events["read_level"] = {
                        "trigger": "level_control_cluster",
                        "script": (
                            f"import urllib.request, json\n"
                            f"try:\n"
                            f"    response = urllib.request.urlopen('{base}/api/level?id={dev_id}')\n"
                            f"    data = json.loads(response.read().decode('utf-8'))\n"
                            f"    print(data.get('level', 0))\n"
                            f"except Exception:\n"
                            f"    print(0)"
                        ),
                    }

                if has_color_temp:
                    events["set_color_temperature"] = {
                        "trigger": "color_control_cluster",
                        "script": (
                            f"import sys, urllib.request\n"
                            f"mireds = int(sys.argv[1]) if len(sys.argv) > 1 else 250\n"
                            f"urllib.request.urlopen(f'{base}/api/mired?id={dev_id}&mireds={{mireds}}')"
                        ),
                    }
                    events["read_color_temperature"] = {
                        "trigger": "color_control_cluster",
                        "script": (
                            f"import urllib.request, json\n"
                            f"try:\n"
                            f"    response = urllib.request.urlopen('{base}/api/mired?id={dev_id}')\n"
                            f"    data = json.loads(response.read().decode('utf-8'))\n"
                            f"    print(data.get('mireds', 0))\n"
                            f"except Exception:\n"
                            f"    print(0)"
                        ),
                    }

            if hw_type != "unknown":
                metadata.append({
                    "node_id": dev_id,
                    "name": name,
                    "hardware_type": hw_type,
                    "events": events,
                })

        return {
            "bridge": {
                "id": "matter_bridge_http",
                "type": "lighting_controller",
                "network_host": host,
                "network_port": port,
            },
            "devices": metadata,
        }
