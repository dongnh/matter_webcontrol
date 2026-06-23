"""Logical-bridge SSE occupancy forwarding.

A device that lives on a remote logical bridge must have its OWN occupancy SSE
stream proxied by ``/api/subscribe`` rather than the local Matter-fabric feed —
otherwise presence sensors hosted on logical bridges (e.g. matter-mac-presence,
matter-appletv-presence) never reach subscribers. These tests pin that routing
and the untimed stream the proxy relies on.
"""

import urllib.request

import pytest

from cli.logic_bridge import LogicalBridgeClient
from tests.fakes import StubLogicalClient


def test_open_stream_builds_untimed_get(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["values"] = list(dict(req.header_items()).values())
        captured["timeout"] = timeout
        return iter([])  # caller only needs an iterable / closeable

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    client = LogicalBridgeClient("10.0.0.5", 8092, api_key="secretk")
    client.open_stream("/api/subscribe", {"id": "dev_x"})

    assert captured["url"] == "http://10.0.0.5:8092/api/subscribe?id=dev_x"
    assert captured["method"] == "GET"
    # No read timeout, so the SSE stream stays open.
    assert captured["timeout"] is None
    # The api-key travels in a header, never the URL.
    assert "secretk" in captured["values"]
    assert "secretk" not in captured["url"]


@pytest.mark.asyncio
async def test_subscribe_routes_logical_first(
    make_client, logical_manager, monkeypatch
):
    from fastapi.responses import JSONResponse

    from cli import server as srv

    seen = {}

    async def fake_proxy(request, resolved, client):
        seen["resolved"] = resolved
        seen["client"] = client
        return JSONResponse({"proxied": resolved})

    monkeypatch.setattr(srv, "_subscribe_logical", fake_proxy)

    stub = StubLogicalClient(
        "peer:1",
        [
            {
                "id": "dev_logsse01",
                "node_id": "peer:1",
                "endpoint_id": 1,
                "states": {"occupancy": 0},
                "names": [],
            }
        ],
    )
    client, _bridge, _ctrl = make_client(logical=logical_manager(stub))

    resp = await client.get("/api/subscribe", params={"id": "dev_logsse01"})
    assert resp.status_code == 200
    assert resp.json() == {"proxied": "dev_logsse01"}
    assert seen["resolved"] == "dev_logsse01"
    assert seen["client"] is stub
