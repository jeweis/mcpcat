"""诊断只读、凭证来源隔离及错误分类。"""

from copy import deepcopy
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2
import pytest

from app.services import inspector_auth as auth
from app.services.oauth_probe import OAuthProbeError, ProbeResult


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_machine_diagnosis_uses_explicit_connection_not_old_token(
    source, monkeypatch, fail
):
    source["config"]["oauth"]["grant_type"] = "client_credentials"
    before = deepcopy(source)
    calls = []

    @asynccontextmanager
    async def connect():
        calls.append(True)
        if fail:
            raise RuntimeError("sensitive-private-key")
        yield SimpleNamespace(protocol_version="2026-07-28")

    monkeypatch.setattr(auth, "make_proxy_client_factory", lambda config: connect)
    probe = AsyncMock()
    monkeypatch.setattr(auth, "probe_mcp_auth", probe)
    snapshot = auth.auth_snapshot(source)
    assert snapshot["machine_auth"] is True
    assert snapshot["credential_usable"] is False
    assert calls == []
    result = await auth.diagnose_upstream(source)
    assert result["status"] == ("error" if fail else "reachable")
    assert calls == [True]
    probe.assert_not_called()
    assert "sensitive-private-key" not in str(result)
    assert source == before


@pytest.fixture
def source():
    return {
        "auth_status": "authorized",
        "config": {
            "type": "streamable-http",
            "url": "https://example.test/mcp",
            "headers": {"Authorization": "configured-static-secret"},
            "oauth": {"token": {"access_token": "upstream-only-secret"}},
        },
    }


@pytest.mark.asyncio
async def test_probe_uses_source_credentials_without_writes(source, monkeypatch):
    before = deepcopy(source)
    probe = AsyncMock(return_value=ProbeResult(protocol_version="2026-07-28"))
    monkeypatch.setattr(auth, "probe_mcp_auth", probe)
    result = await auth.diagnose_upstream(source)
    assert result["status"] == "reachable"
    assert probe.call_args.args[1] == {"Authorization": "Bearer upstream-only-secret"}
    assert "secret" not in str(result) and "example.test" not in str(result)
    assert source == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata,expected",
    [
        ({}, "authorization_unavailable"),
        ({"issuer": "secret"}, "authorization_required"),
    ],
)
async def test_challenge_does_not_mean_gateway_key_invalid(
    source, monkeypatch, metadata, expected
):
    challenge = httpx2.Response(401)
    monkeypatch.setattr(
        auth, "probe_mcp_auth", AsyncMock(return_value=ProbeResult(challenge=challenge))
    )
    monkeypatch.setattr(
        auth, "discover_oauth_endpoints", AsyncMock(return_value=metadata)
    )
    result = await auth.diagnose_upstream(source)
    assert result["status"] == expected
    assert "secret" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category", ["timeout", "connection", "protocol_version", "redirect"]
)
async def test_errors_are_not_no_auth(source, monkeypatch, category):
    monkeypatch.setattr(
        auth, "probe_mcp_auth", AsyncMock(side_effect=OAuthProbeError(category))
    )
    result = await auth.diagnose_upstream(source)
    assert result["status"] == "error" and result["category"] == category


@pytest.mark.asyncio
async def test_stdio_diagnosis_does_not_spawn_process(monkeypatch):
    probe = AsyncMock()
    monkeypatch.setattr(auth, "probe_mcp_auth", probe)
    result = await auth.diagnose_upstream({"config": {"type": "stdio"}})
    assert result["status"] == "not_applicable"
    probe.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("era", ["modern", "legacy"])
async def test_diagnostic_real_protocol_discovery(monkeypatch, era):
    from fastmcp import FastMCP
    from test_oauth_discovery import clients, URL
    from test_proxy_cross_generation import single_era

    server = FastMCP("diagnostic real discovery")
    app = server.http_app(path="/mcp", json_response=True)
    methods = []
    clients(monkeypatch, httpx2.ASGITransport(app=single_era(app, era, methods)))
    async with app.lifespan(app):
        result = await auth.diagnose_upstream(
            {
                "config": {
                    "type": "streamable-http",
                    "url": URL,
                }
            }
        )
    assert result["status"] == "reachable"
    assert result["protocol"] == ("2026-07-28" if era == "modern" else "2025-11-25")
    assert not any(method.startswith("tools/") for method in methods)


@pytest.mark.asyncio
async def test_diagnostic_real_oauth_discovery_separates_credentials(monkeypatch):
    from test_oauth_discovery import (
        clients,
        URL,
        PRM,
        authorization_metadata,
        protected_metadata,
    )

    def handler(request):
        if str(request.url) == URL:
            assert request.headers["Authorization"] == "Bearer source-fixture-secret"
            return httpx2.Response(
                401,
                headers={
                    "WWW-Authenticate": f'Bearer resource_metadata="{PRM}"',
                },
            )
        assert "Authorization" not in request.headers
        assert "Mcpcat-Key" not in request.headers
        if str(request.url) == PRM:
            return httpx2.Response(200, json=protected_metadata())
        if request.url.path == "/.well-known/oauth-authorization-server/tenant":
            return httpx2.Response(200, json=authorization_metadata())
        return httpx2.Response(404)

    clients(monkeypatch, httpx2.MockTransport(handler))
    result = await auth.diagnose_upstream(
        {
            "config": {
                "type": "streamable-http",
                "url": URL,
                "oauth": {"token": {"access_token": "source-fixture-secret"}},
            }
        }
    )
    assert result["status"] == "authorization_required"
    assert "secret" not in str(result) and "fixture" not in str(result)
