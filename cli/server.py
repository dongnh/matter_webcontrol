import argparse
import asyncio
import datetime
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from cli.core import DeviceController
from cli.logic_bridge import LogicalBridgeManager
from cli.matter_bridge import MatterBridgeServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

controller: Optional[DeviceController] = None

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class NamePayload(BaseModel):
    id: str
    name: str

class ControlPayload(BaseModel):
    id: str
    brightness: Optional[float] = None
    temperature: Optional[int] = None

class LevelPayload(BaseModel):
    id: str
    level: Optional[int] = None

class MiredPayload(BaseModel):
    id: str
    mireds: Optional[int] = None

class BatchPayload(BaseModel):
    actions: list[dict]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_params(request: Request, payload, fields: list[str]) -> dict:
    if request.method == "POST" and payload:
        return {f: getattr(payload, f, None) for f in fields}
    return {f: request.query_params.get(f) for f in fields}


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
    logical.load_cache()

    controller = DeviceController(bridge, logical)

    if bridge.is_ready():
        bridge._update_cache()

    count = logical.refresh_bridges()
    logging.info(f"Startup sync complete. Refreshed Matter cache and {count} logical bridges.")

    yield

    await bridge.shutdown(app)


app = FastAPI(title="Matter Web Controller", lifespan=lifespan)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    api_key = getattr(app.state, "api_key", None)
    if api_key and request.headers.get("X-API-Key") != api_key:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)

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


@app.get("/api/status")
async def get_status_api():
    return controller.get_status()


@app.get("/api/toggle")
async def toggle_api(id: str):
    return await _wrap_async(controller.toggle(id))


@app.api_route("/api/name", methods=["GET", "POST"])
async def set_name_api(request: Request, payload: Optional[NamePayload] = None):
    params = _get_params(request, payload, ["id", "name"])
    if not params["id"] or not params["name"]:
        raise HTTPException(status_code=400, detail="Missing id or name parameter")
    return _wrap(controller.set_name, params["id"], params["name"], status=409)


@app.get("/api/name/remove")
async def remove_name_api(id: str, name: str):
    return _wrap(controller.remove_name, id, name)


@app.get("/api/bridge")
async def add_bridge_api(ip: str, port: int, api_key: Optional[str] = None):
    return _wrap(controller.add_bridge, ip, port, api_key)


@app.get("/api/bridge/remove")
async def remove_bridge_api(ip: str, port: int):
    return _wrap(controller.remove_bridge, ip, port)


@app.get("/api/register")
async def register_api(code: str, ip: Optional[str] = None, name: Optional[str] = None):
    return await _wrap_async(controller.register_device(code, ip, name))


@app.get("/api/unregister")
async def unregister_api(node_id: int):
    return await _wrap_async(controller.unregister_node(node_id))


@app.api_route("/api/set", methods=["GET", "POST"])
async def set_device_api(request: Request, payload: Optional[ControlPayload] = None):
    params = _get_params(request, payload, ["id", "brightness", "temperature"])
    if not params["id"]:
        raise HTTPException(status_code=400, detail="Missing device id")

    brightness = float(params["brightness"]) if params["brightness"] is not None else None
    temperature = int(params["temperature"]) if params["temperature"] is not None else None

    return await _wrap_async(controller.set_device(params["id"], brightness, temperature))


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

    return await _wrap_async(controller.set_level(params["id"], int(params["level"])))


@app.api_route("/api/mired", methods=["GET", "POST"])
async def mired_api(request: Request, payload: Optional[MiredPayload] = None):
    params = _get_params(request, payload, ["id", "mireds"])
    if not params["id"]:
        raise HTTPException(status_code=400, detail="Missing device id")

    if params["mireds"] is None:
        return _wrap(controller.get_mired, params["id"])

    return await _wrap_async(controller.set_mired(params["id"], int(params["mireds"])))


@app.get("/api/subscribe")
async def subscribe_api(request: Request, id: str):
    resolved = id
    controller.bridge.occupancy_subscribers.setdefault(resolved, [])

    queue = asyncio.Queue()
    controller.bridge.occupancy_subscribers[resolved].append(queue)

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
                iso = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()
                payload = _json.dumps({"id": resolved, "occupancy": state, "timestamp": iso})
                yield f"data: {payload}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            subs = controller.bridge.occupancy_subscribers.get(resolved, [])
            if queue in subs:
                subs.remove(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/refresh")
async def refresh_api():
    return _wrap(controller.refresh)


@app.get("/api/metadata")
async def metadata_api(request: Request):
    host = request.url.hostname or "127.0.0.1"
    port = request.url.port or 8080
    return controller.get_metadata(host, port)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Matter API Web Server")
    parser.add_argument("--port", type=int, default=8080, help="Web server port")
    parser.add_argument("--host", type=str, default="127.0.0.1",
                        help="Bind address (default: 127.0.0.1; use 0.0.0.0 to expose on LAN)")
    parser.add_argument("--fabric", type=str, default=None, help="Matter fabric label")
    parser.add_argument("--api-key", type=str, default=os.environ.get("MATTER_SRV_KEY"),
                        help="Require X-API-Key header (or set MATTER_SRV_KEY env var)")
    args = parser.parse_args()

    if args.host != "127.0.0.1" and not args.api_key:
        logging.warning(
            "Server bound to %s without --api-key. Anyone on the network can control "
            "your devices and commission new ones. Set MATTER_SRV_KEY or pass --api-key.",
            args.host,
        )

    app.state.port = args.port
    app.state.fabric_label = args.fabric
    app.state.api_key = args.api_key
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
