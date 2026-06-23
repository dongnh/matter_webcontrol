"""HTTP-edge regression net — ports the single-instance smoke.sh assertions
to in-process httpx ASGITransport (no socket binding)."""

import httpx
import pytest

from tests.conftest import TEST_KEY


# -- auth -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_key_401(make_client):
    _c, _b, _ctrl = make_client("A", api_key=TEST_KEY)
    from cli import server as srv

    transport = httpx.ASGITransport(app=srv.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
        resp = await raw.get("/api/status")
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


@pytest.mark.asyncio
async def test_wrong_key_401(make_client):
    make_client("A", api_key=TEST_KEY)
    from cli import server as srv

    transport = httpx.ASGITransport(app=srv.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"X-API-Key": "nope"}
    ) as raw:
        resp = await raw.get("/api/status")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_good_key_200(client):
    resp = await client.get("/api/status")
    assert resp.status_code == 200
    assert "lights_on" in resp.json()


# -- standalone queries / control -------------------------------------------

@pytest.mark.asyncio
async def test_devices_contains_fixture(client):
    resp = await client.get("/api/devices")
    ids = [d["id"] for d in resp.json()]
    assert "dev_aaaa0001" in ids


@pytest.mark.asyncio
async def test_status_light_counts(client):
    body = (await client.get("/api/status")).json()
    assert body["lights_on"] == 1
    assert body["lights_off"] == 1


@pytest.mark.asyncio
async def test_toggle_then_status(client):
    resp = await client.get("/api/toggle", params={"id": "dev_aaaa0001"})
    assert resp.json()["status"] == "success"
    body = (await client.get("/api/status")).json()
    assert body["lights_on"] == 0


@pytest.mark.asyncio
async def test_set_mired_clamp_low(client):
    resp = await client.post("/api/mired", json={"id": "dev_aaaa0002", "mireds": 100})
    assert resp.json()["mireds"] == 153


@pytest.mark.asyncio
async def test_set_mired_clamp_high(client):
    resp = await client.post("/api/mired", json={"id": "dev_aaaa0002", "mireds": 999})
    assert resp.json()["mireds"] == 500


@pytest.mark.asyncio
async def test_unknown_device_404(client):
    resp = await client.get("/api/level", params={"id": "dev_zzzz"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_batch_per_action_results(client):
    resp = await client.post(
        "/api/batch",
        json={
            "actions": [
                {"id": "dev_aaaa0001", "brightness": 1.0},
                {"id": "dev_aaaa0002", "brightness": 0.7},
                {"id": "dev_zzzz", "brightness": 1.0},
            ]
        },
    )
    results = resp.json()
    assert len(results) == 3
    by_id = {r.get("id"): r for r in results}
    assert by_id["dev_aaaa0002"]["status"] == "success"
    assert by_id["dev_zzzz"]["status"] == "error"


# -- metadata ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_metadata_api_version(client):
    meta = (await client.get("/api/metadata")).json()
    assert meta["bridge"]["api_version"] == "2"
    assert all("script" not in d for d in meta["devices"])
    assert any("capabilities" in d for d in meta["devices"])
