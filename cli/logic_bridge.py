"""HTTP federation client for remote Matter Web Controller instances.

Each remote bridge exposes the same REST API as the local server. This module
calls those endpoints directly — no embedded scripts, no code execution.
"""

import concurrent.futures
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


class LogicalBridgeClient:
    """REST client for a remote Matter Web Controller."""

    def __init__(self, host: str, port: int, api_key: Optional[str] = None):
        self.host = host
        self.port = port
        self.api_key = api_key
        self.base_url = f"http://{host}:{port}"
        self.devices: Dict[str, Dict[str, Any]] = {}

    def _request(
        self,
        path: str,
        method: str = "GET",
        query: Optional[dict] = None,
        body: Optional[dict] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            clean = {k: v for k, v in query.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)

        data = None
        headers: Dict[str, str] = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None

    def refresh(self) -> None:
        """Pull device list from the remote and cache it locally."""
        data = self._request("/api/devices")
        self.devices = {dev["id"]: dev for dev in data if "id" in dev}

    def set_level(self, device_id: str, level: int) -> None:
        self._request(
            "/api/level", method="POST",
            body={"id": device_id, "level": int(level)},
        )

    def set_mired(self, device_id: str, mireds: int) -> None:
        self._request(
            "/api/mired", method="POST",
            body={"id": device_id, "mireds": int(mireds)},
        )

    def set_brightness(self, device_id: str, brightness: float) -> None:
        self._request(
            "/api/set", method="POST",
            body={"id": device_id, "brightness": float(brightness)},
        )

    def get_ac(self, device_id: str) -> Any:
        return self._request("/api/ac", query={"id": device_id})

    def set_ac(
        self,
        device_id: str,
        on: Optional[bool] = None,
        mode: Optional[int] = None,
        setpoint: Optional[float] = None,
        fan_speed: Optional[int] = None,
    ) -> Any:
        body: Dict[str, Any] = {"id": device_id}
        if on is not None:
            body["on"] = bool(on)
        if mode is not None:
            body["system_mode"] = int(mode)
        if setpoint is not None:
            body["setpoint"] = float(setpoint)
        if fan_speed is not None:
            body["fan_speed"] = int(fan_speed)
        return self._request("/api/ac", method="POST", body=body)


class LogicalBridgeManager:
    """Registry of remote logical bridges with persistent cache."""

    def __init__(self, cache_file: str = "bridge_cache.json"):
        self.registry: Dict[str, LogicalBridgeClient] = {}
        self.cache_file = cache_file

    def load_cache(self) -> None:
        if not os.path.exists(self.cache_file):
            return
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        for cfg in data.values():
            try:
                self.add_bridge(
                    cfg["ip"], int(cfg["port"]),
                    api_key=cfg.get("api_key"), persist=False,
                )
            except Exception:
                pass  # skip offline bridges

    def _save_cache(self) -> None:
        data = {
            nid: {"ip": c.host, "port": c.port, "api_key": c.api_key}
            for nid, c in self.registry.items()
        }
        tmp = self.cache_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self.cache_file)

    def add_bridge(
        self,
        ip: str,
        port: int,
        api_key: Optional[str] = None,
        persist: bool = True,
    ) -> str:
        node_id = f"{ip}:{port}"
        client = LogicalBridgeClient(ip, port, api_key)
        client.refresh()
        self.registry[node_id] = client
        if persist:
            self._save_cache()
        return node_id

    def remove_bridge(self, ip: str, port: int) -> str:
        node_id = f"{ip}:{port}"
        if node_id not in self.registry:
            raise KeyError(f"Bridge {node_id} not found")
        del self.registry[node_id]
        self._save_cache()
        return node_id

    def refresh_bridges(self) -> Dict[str, int]:
        """Refresh every registered bridge concurrently.

        Returns {"refreshed": n, "failed": m}. Each failure is logged with its
        node id; one slow/unreachable peer no longer serializes the others.
        """
        items = list(self.registry.items())
        if not items:
            return {"refreshed": 0, "failed": 0}

        refreshed = 0
        failed = 0
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(8, len(items))
        ) as ex:
            futures = {ex.submit(c.refresh): nid for nid, c in items}
            for fut in concurrent.futures.as_completed(futures):
                nid = futures[fut]
                try:
                    fut.result()
                    refreshed += 1
                except Exception as e:
                    failed += 1
                    logging.warning("Logical bridge %s refresh failed: %s", nid, e)
        return {"refreshed": refreshed, "failed": failed}

    def get_all_devices(self) -> Dict[str, Any]:
        """Return cached device list across all bridges (no HTTP per call)."""
        aggregated = []
        for node_id, client in self.registry.items():
            for dev in client.devices.values():
                aggregated.append({
                    "id": dev["id"],
                    "node_id": node_id,
                    "endpoint_id": dev.get("endpoint_id"),
                    "states": dev.get("states", {}),
                    "names": dev.get("names", []),
                })
        return {"total_devices": len(aggregated), "devices": aggregated}
