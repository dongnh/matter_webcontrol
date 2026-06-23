import argparse
import asyncio
import datetime
import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from cli import __version__, auth, paths
from cli.core import DeviceController
from cli.logic_bridge import LogicalBridgeManager
from cli.matter_bridge import MatterBridgeServer
from cli.schemas import (
    ACPayload,
    BatchPayload,
    BridgePayload,
    BridgeRemovePayload,
    ControlPayload,
    LevelPayload,
    MiredPayload,
    NamePayload,
    NameRemovePayload,
    RegisterPayload,
    TogglePayload,
    UnregisterPayload,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# Unauthenticated read-only endpoints (liveness + version).
PUBLIC_PATHS = {"/health", "/version"}

controller: DeviceController = None  # type: ignore[assignment]  # set in lifespan

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_params(request: Request, payload, fields: list[str]) -> dict:
    if request.method == "POST" and payload:
        return {f: getattr(payload, f, None) for f in fields}
    return {f: request.query_params.get(f) for f in fields}


def _coerce(value, caster):
    """Coerce a query-string value, turning a bad value into 400 (not 500)."""
    if value is None or isinstance(value, caster):
        return value
    try:
        return caster(value)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400, detail=f"Invalid parameter value: {value!r}"
        )


def _wrap(fn, *args, status=400, **kwargs):
    """Call a core function, converting exceptions to HTTPException."""
    try:
        return fn(*args, **kwargs)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _wrap_async(coro, status=400):
    """Await a core coroutine, converting exceptions to HTTPException."""
    try:
        return await coro
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    global controller
    port = getattr(app.state, "port", 8080)
    fabric_label = getattr(app.state, "fabric_label", None)

    bridge = MatterBridgeServer(port)
    await bridge.initialize(app, fabric_label=fabric_label)

    logical = LogicalBridgeManager()
    logical.local_host = getattr(app.state, "host", "127.0.0.1")
    logical.local_port = port
    logical.load_cache()

    controller = DeviceController(bridge, logical)

    if bridge.is_ready():
        bridge.sync()

    result = logical.refresh_bridges()
    logging.info(
        "Startup sync complete. Refreshed Matter cache and %d logical bridges (%d failed).",
        result["refreshed"],
        result["failed"],
    )

    # Self-heal logical bridges that were offline at startup (e.g. a Casambi
    # bridge whose Bluetooth hadn't connected when this server booted).
    # load_cache() silently skips a cached bridge whose /api/devices refused
    # the connection, so retry the cached-but-missing/empty ones in the
    # background until they register, then stop.
    async def _heal_logical_bridges():
        for _ in range(40):  # ~20 min: covers slow Bluetooth/cloud bridge startup
            await asyncio.sleep(30)
            try:
                with open(logical.cache_file, "r", encoding="utf-8") as f:
                    desired = json.load(f)
            except Exception:
                continue
            # A bridge needs healing if it is unregistered, OR registered but
            # holding zero devices (listening before its backend finished
            # connecting). registry/cache are both keyed by "ip:port".
            missing = [
                cfg
                for nid, cfg in desired.items()
                if not getattr(logical.registry.get(nid), "devices", None)
            ]
            if not missing:
                return
            for cfg in missing:
                try:
                    await asyncio.to_thread(
                        logical.add_bridge,
                        cfg["ip"],
                        int(cfg["port"]),
                        api_key=cfg.get("api_key"),
                        persist=False,
                    )
                    logging.info(
                        "Self-healed logical bridge %s:%s", cfg["ip"], cfg["port"]
                    )
                except Exception:
                    pass  # still offline; retry next round

    heal_task = asyncio.create_task(_heal_logical_bridges())

    yield

    heal_task.cancel()

    await bridge.shutdown(app)


app = FastAPI(title="Matter Web Controller", lifespan=lifespan)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.url.path not in PUBLIC_PATHS:
        api_key = getattr(app.state, "api_key", None)
        if not auth.check_api_key(request.headers.get("X-API-Key"), api_key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    # Logs method/path/status (+ device id from the query). Never logs headers
    # or the body, so the X-API-Key and body secrets are not recorded (G6).
    response = await call_next(request)
    dev_id = request.query_params.get("id")
    logging.info(
        "%s %s -> %s%s",
        request.method,
        request.url.path,
        response.status_code,
        f" id={dev_id}" if dev_id else "",
    )
    return response


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/devices")
async def get_devices_api():
    return controller.get_devices()


@app.get("/api/lights")
async def get_lights_api():
    return controller.get_lights()


@app.get("/api/sensors")
async def get_sensors_api():
    return controller.get_sensors()


@app.get("/api/sensor")
async def get_sensor_api(id: str):
    return _wrap(controller.get_sensor, id)


@app.get("/api/climate")
async def climate_api(id: Optional[str] = None):
    if id:
        return _wrap(controller.get_climate_one, id)
    return controller.get_climate()


@app.get("/api/status")
async def get_status_api():
    return controller.get_status()


@app.post("/api/toggle")
async def toggle_api(payload: TogglePayload):
    return await _wrap_async(controller.toggle(payload.id))


@app.post("/api/name")
async def set_name_api(payload: NamePayload):
    return _wrap(controller.set_name, payload.id, payload.name, status=409)


@app.post("/api/name/remove")
async def remove_name_api(payload: NameRemovePayload):
    return _wrap(controller.remove_name, payload.id, payload.name)


@app.post("/api/bridge")
async def add_bridge_api(payload: BridgePayload):
    # api_key arrives in the JSON body, never the URL (S1). add_bridge does a
    # blocking federation fetch — offload off the event loop.
    return await _wrap_async(
        asyncio.to_thread(
            controller.add_bridge, payload.ip, payload.port, payload.api_key
        )
    )


@app.post("/api/bridge/remove")
async def remove_bridge_api(payload: BridgeRemovePayload):
    # remove_bridge does blocking cache file I/O — offload off the event loop.
    return await _wrap_async(
        asyncio.to_thread(controller.remove_bridge, payload.ip, payload.port)
    )


@app.post("/api/register")
async def register_api(payload: RegisterPayload):
    # pairing code in the body, never the URL (S3).
    return await _wrap_async(
        controller.register_device(payload.code, payload.ip, payload.name)
    )


@app.post("/api/unregister")
async def unregister_api(payload: UnregisterPayload):
    return await _wrap_async(controller.unregister_node(payload.node_id))


@app.api_route("/api/set", methods=["GET", "POST"])
async def set_device_api(request: Request, payload: Optional[ControlPayload] = None):
    params = _get_params(request, payload, ["id", "brightness", "temperature"])
    if not params["id"]:
        raise HTTPException(status_code=400, detail="Missing device id")

    brightness = _coerce(params["brightness"], float)
    temperature = _coerce(params["temperature"], int)

    return await _wrap_async(
        controller.set_device(params["id"], brightness, temperature)
    )


@app.post("/api/batch")
async def batch_api(payload: BatchPayload):
    return await _wrap_async(controller.batch_control(payload.actions))


@app.api_route("/api/level", methods=["GET", "POST"])
async def level_api(request: Request, payload: Optional[LevelPayload] = None):
    params = _get_params(request, payload, ["id", "level"])
    if not params["id"]:
        raise HTTPException(status_code=400, detail="Missing device id")

    if params["level"] is None:
        return _wrap(controller.get_level, params["id"])

    return await _wrap_async(
        controller.set_level(params["id"], _coerce(params["level"], int))
    )


@app.api_route("/api/mired", methods=["GET", "POST"])
async def mired_api(request: Request, payload: Optional[MiredPayload] = None):
    params = _get_params(request, payload, ["id", "mireds"])
    if not params["id"]:
        raise HTTPException(status_code=400, detail="Missing device id")

    if params["mireds"] is None:
        return _wrap(controller.get_mired, params["id"])

    return await _wrap_async(
        controller.set_mired(params["id"], _coerce(params["mireds"], int))
    )


async def _subscribe_logical(request: Request, resolved: str, client):
    """Proxy a remote logical bridge's occupancy SSE stream to this client.

    LogicalBridgeClient uses a blocking urllib stream, so a background thread
    reads its lines and hands them to the async generator via a queue. Lines
    are forwarded verbatim to preserve SSE framing.
    """
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()
    stop = threading.Event()
    _DONE = object()

    def reader():
        stream = None
        try:
            stream = client.open_stream("/api/subscribe", {"id": resolved})
            for raw in stream:
                if stop.is_set():
                    break
                line = raw.decode("utf-8", errors="replace")
                loop.call_soon_threadsafe(queue.put_nowait, line)
        except Exception as exc:  # surface upstream loss as a stream end
            logging.warning("logical subscribe [%s] dropped: %s", resolved, exc)
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            loop.call_soon_threadsafe(queue.put_nowait, _DONE)

    threading.Thread(target=reader, daemon=True).start()

    async def stream_gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if item is _DONE:
                    # Upstream logical bridge went away (host slept / lost the
                    # network). A device we can no longer reach is, by
                    # definition, unoccupied — emit a synthetic 0 so
                    # subscribers fall back to "absent" instead of holding the
                    # last value.
                    iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    payload = json.dumps(
                        {"id": resolved, "occupancy": 0, "timestamp": iso}
                    )
                    yield f"data: {payload}\n\n"
                    break
                yield item
        finally:
            stop.set()

    return StreamingResponse(stream_gen(), media_type="text/event-stream")


@app.get("/api/subscribe")
async def subscribe_api(request: Request, id: str):
    resolved = id

    # Logical-first: if this device lives on a remote logical bridge, forward
    # its own occupancy SSE stream rather than the local Matter-fabric feed.
    kind, _dev, client = controller._route(resolved)
    if kind == "logical" and client is not None:
        return await _subscribe_logical(request, resolved, client)

    queue = controller.bridge.subscribe_occupancy(resolved)

    async def stream():
        import json as _json

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    state, ts = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                iso = datetime.datetime.fromtimestamp(
                    ts, datetime.timezone.utc
                ).isoformat()
                payload = _json.dumps(
                    {"id": resolved, "occupancy": state, "timestamp": iso}
                )
                yield f"data: {payload}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            controller.bridge.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/acs")
async def list_acs_api():
    return controller.get_acs()


@app.api_route("/api/ac", methods=["GET", "POST"])
async def ac_api(request: Request, payload: Optional[ACPayload] = None):
    params = _get_params(
        request, payload, ["id", "on", "mode", "system_mode", "setpoint", "fan_speed"]
    )
    if not params["id"]:
        raise HTTPException(status_code=400, detail="Missing device id")

    if all(
        params[k] is None
        for k in ("on", "mode", "system_mode", "setpoint", "fan_speed")
    ):
        return _wrap(controller.get_ac, params["id"])

    def parse_bool(v):
        if v is None or isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes", "on")
        return bool(v)

    on = parse_bool(params["on"])
    # system_mode is the documented alias; mode is canonical (API4).
    mode_raw = (
        params["system_mode"] if params["system_mode"] is not None else params["mode"]
    )
    mode = _coerce(mode_raw, int)
    setpoint = _coerce(params["setpoint"], float)
    fan_speed = _coerce(params["fan_speed"], int)

    return await _wrap_async(
        controller.set_ac(
            params["id"], on=on, mode=mode, setpoint=setpoint, fan_speed=fan_speed
        )
    )


@app.post("/api/refresh")
async def refresh_api():
    # refresh fans out blocking federation HTTP — offload off the event loop.
    return await _wrap_async(asyncio.to_thread(controller.refresh))


@app.get("/api/metadata")
async def metadata_api(request: Request):
    host = request.url.hostname or "127.0.0.1"
    port = request.url.port or 8080
    return controller.get_metadata(host, port)


# -- Unauthenticated liveness + version (PUBLIC_PATHS) ----------------------


@app.get("/health")
async def health_api():
    ready = bool(controller and controller.bridge and controller.bridge.is_ready())
    return {"status": "ok" if ready else "starting", "bridge_ready": ready}


@app.get("/version")
async def version_api():
    return {"version": __version__}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Matter API Web Server")
    parser.add_argument("--port", type=int, default=8080, help="Web server port")
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1; use 0.0.0.0 to expose on LAN)",
    )
    parser.add_argument("--fabric", type=str, default=None, help="Matter fabric label")
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.environ.get("MATTER_SRV_KEY"),
        help="Require X-API-Key header (or set MATTER_SRV_KEY env var)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Directory for caches + Matter fabric storage "
        "(or set MATTER_DATA_DIR; defaults to the current directory)",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Allow binding to a non-loopback host without an API key",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=os.environ.get("MATTER_LOG_LEVEL", "INFO"),
        help="Logging level (or set MATTER_LOG_LEVEL; default INFO)",
    )
    args = parser.parse_args()

    logging.getLogger().setLevel(args.log_level.upper())

    data_dir = paths.set_data_dir(args.data_dir)
    logging.info("Data directory: %s", data_dir)
    logging.info("Matter fabric storage: %s", paths.matter_storage())

    # Refuse an unauthenticated non-loopback bind unless explicitly overridden (S8).
    auth.require_secure_bind(args.host, args.api_key, args.insecure)

    app.state.host = args.host
    app.state.port = args.port
    app.state.fabric_label = args.fabric
    app.state.api_key = args.api_key
    # access logging is handled by access_log_middleware (redacts secrets).
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
