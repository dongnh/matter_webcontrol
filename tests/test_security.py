"""Step 8: POST-only mutations, unauth /health + /version, typed-coercion 400s,
and SSRF / self-registration rejection."""

import httpx
import pytest

from cli.logic_bridge import LogicalBridgeManager
from tests.conftest import TEST_KEY

# -- POST-only mutations (S3) -----------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/toggle",
        "/api/bridge",
        "/api/refresh",
        "/api/register",
        "/api/unregister",
        "/api/name",
        "/api/name/remove",
    ],
)
async def test_mutations_reject_get(client, path):
    resp = await client.get(path, params={"id": "dev_aaaa0001"})
    assert resp.status_code == 405


@pytest.mark.asyncio
async def test_bridge_api_key_in_body(client):
    # The route must accept api_key in the body (not URL). Targeting a refused
    # loopback port yields a non-405 (503/400), proving the body is parsed.
    resp = await client.post(
        "/api/bridge", json={"ip": "127.0.0.1", "port": 1, "api_key": "secret"}
    )
    assert resp.status_code != 405


# -- unauthenticated health/version (API8) ----------------------------------


@pytest.mark.asyncio
async def test_health_and_version_unauthenticated(make_client):
    make_client("A", api_key=TEST_KEY)
    from cli import server as srv

    transport = httpx.ASGITransport(app=srv.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
        health = await raw.get("/health")
        assert health.status_code == 200
        assert health.json()["bridge_ready"] is True
        version = await raw.get("/version")
        assert version.status_code == 200
        assert "version" in version.json()


# -- typed coercion (API11): bad query value -> 400, not 500 ----------------


@pytest.mark.asyncio
async def test_bad_query_value_is_400(client):
    resp = await client.get(
        "/api/set", params={"id": "dev_aaaa0001", "brightness": "abc"}
    )
    assert resp.status_code == 400


# -- SSRF / self-registration (S2, G2) --------------------------------------


def _mgr(tmp_path, **identity):
    mgr = LogicalBridgeManager(cache_file=str(tmp_path / "bridge.json"))
    for k, v in identity.items():
        setattr(mgr, k, v)
    return mgr


def test_ssrf_public_ip_rejected(tmp_path):
    with pytest.raises(ValueError):
        _mgr(tmp_path).add_bridge("8.8.8.8", 8080)


def test_invalid_port_rejected(tmp_path):
    with pytest.raises(ValueError):
        _mgr(tmp_path).add_bridge("192.168.1.5", 70000)


def test_self_registration_rejected(tmp_path):
    mgr = _mgr(tmp_path, local_host="127.0.0.1", local_port=8080)
    with pytest.raises(ValueError):
        mgr.add_bridge("127.0.0.1", 8080)
    with pytest.raises(ValueError):
        mgr.add_bridge("localhost", 8080)


def test_private_hostname_allowed_through_validation(tmp_path):
    # mDNS/LAN hostnames must pass validation (the subsequent refresh may still
    # fail on the network, but that's not a validation rejection).
    mgr = _mgr(tmp_path)
    # Validate-only: monkeypatch the network refresh away so this stays fast.
    assert mgr._validate_target("nonexistent-peer.invalid", 8080) == 8080
