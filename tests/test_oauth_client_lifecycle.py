"""完整本地夹具：DCR/CIMD/旧预注册 → 原 REST 回调 → MCP 调用 → 旧 token 刷新。"""

import base64
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import httpx2
import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.dependencies import get_http_headers
from test_oauth_callbacks import authorize, callback_app, complete, token_client
from test_upstream_apps_proxy import proxy_targets

from app.services import oauth_client_registration as registration
from app.services.mcp_factory import MCPServerFactory
from app.services.server_manager import MCPServerManager

HTTPClient = httpx2.AsyncClient
DOCUMENT = "https://gateway.fixture/oauth/client.json"


def registration_transport(monkeypatch, requests):
    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert "client_secret" not in body
        return httpx2.Response(
            201,
            json={
                "client_id": "registered-client",
                "client_secret": "registered-secret",
                "token_endpoint_auth_method": "client_secret_basic",
                "redirect_uris": body["redirect_uris"],
                "issuer": "https://wire-attacker.fixture",
            },
        )

    monkeypatch.setattr(
        registration,
        "httpx2",
        SimpleNamespace(
            AsyncClient=lambda **kwargs: HTTPClient(
                **kwargs, transport=httpx2.MockTransport(handler)
            ),
            Request=httpx2.Request,
            Response=httpx2.Response,
        ),
    )


def issued_tokens(monkeypatch, kind, query, requests):
    def handler(request):
        requests.append(request)
        values = parse_qs(request.content.decode())
        if kind == "dcr":
            assert (
                request.headers["Authorization"]
                == "Basic "
                + base64.b64encode(b"registered-client:registered-secret").decode()
            )
            assert values["client_id"] == ["registered-client"]
            assert "client_secret" not in values
        elif kind == "cimd":
            assert values["client_id"] == [DOCUMENT]
            assert (
                "client_secret" not in values and "Authorization" not in request.headers
            )
        else:
            assert values["client_id"] == ["fixture-client"]
            assert values["client_secret"] == ["fixture-secret"]
        if values["grant_type"] == ["authorization_code"]:
            proof = (
                base64.urlsafe_b64encode(
                    hashlib.sha256(values["code_verifier"][0].encode()).digest()
                )
                .decode()
                .rstrip("=")
            )
            assert proof == query["code_challenge"][0]
            assert values["redirect_uri"] == query["redirect_uri"]
            return httpx.Response(
                200,
                json={
                    "access_token": "issued-access",
                    "refresh_token": "issued-refresh",
                    "expires_in": 3600,
                },
            )
        assert values["refresh_token"] == ["issued-refresh"]
        return httpx.Response(
            200, json={"access_token": "refreshed-access", "expires_in": 3600}
        )

    token_client(monkeypatch, handler)


def oauth_fixture(info, kind):
    data = info["config"]["oauth"]
    data["unknown"] = {"preserve": ["existing-data"]}
    if kind == "legacy":
        data.pop("issuer", None)
        data["authorization_response_iss_parameter_supported"] = False
    else:
        data.pop("client_id")
        data.pop("client_secret")
        data["registration_endpoint"] = "https://issuer.fixture/register"
        if kind == "cimd":
            data["client_metadata_url"] = DOCUMENT
            data["client_id_metadata_document_supported"] = True


def source_proxy(monkeypatch, info):
    source = FastMCP("oauth-upstream")

    @source.tool()
    def identity() -> str:
        """夹具只回显测试凭证，证明实际 HTTP 出站而非模型字段更新。"""
        return get_http_headers(include={"authorization"}).get(
            "authorization", "absent"
        )

    upstream = source.http_app(path="/mcp", json_response=True)
    proxy_targets(monkeypatch, {info["config"]["url"]: upstream})
    proxy = MCPServerFactory.create_server("fixture", info["config"])
    info["mcp"] = proxy
    return upstream, proxy


def verify_outcome(info, saved, kind, registrations, tokens):
    assert len(registrations) == (1 if kind == "dcr" else 0)
    assert len(tokens) == 2
    assert info["auth_status"] == "authorized"
    assert info["config"]["oauth"]["token"]["refresh_token"] == "issued-refresh"
    assert info["config"]["oauth"]["unknown"] == {"preserve": ["existing-data"]}
    if kind != "legacy":
        assert info["config"]["oauth"]["credentials_issuer"] == "https://issuer.fixture"
        assert info["config"]["oauth"]["token_issuer"] == "https://issuer.fixture"
    assert saved[-1][1].token.access_token == "refreshed-access"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["dcr", "cimd", "legacy"])
@pytest.mark.parametrize("callback", ["auto", "manual"])
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_oauth_registered_callback_and_refresh_reaches_real_mcp(
    callback_app,
    monkeypatch,
    kind,
    callback,
    mode,
):
    app, manager, info, saved = callback_app
    oauth_fixture(info, kind)
    upstream, proxy = source_proxy(monkeypatch, info)
    registrations, tokens, query = [], [], {}
    registration_transport(monkeypatch, registrations)
    issued_tokens(monkeypatch, kind, query, tokens)
    async with upstream.lifespan(upstream), HTTPClient(
        transport=httpx2.ASGITransport(app),
        base_url="https://gateway.fixture",
    ) as http:
        result = await authorize(http, info, callback)
        query.update(parse_qs(urlparse(result["authorize_url"]).query))
        assert (
            await complete(
                http,
                result,
                callback,
                issuer=None if kind == "legacy" else "https://issuer.fixture",
            )
        ).status_code == 200
        async with Client(proxy, mode=mode) as client:
            assert (
                await client.call_tool("identity", {})
            ).data == "Bearer issued-access"
            info["config"]["oauth"]["token"]["expires_at"] = "2000-01-01T00:00:00+00:00"
            await MCPServerManager._detect_auth_status(
                manager, "fixture", info["config"]
            )
            assert (
                await client.call_tool("identity", {})
            ).data == "Bearer refreshed-access"
    verify_outcome(info, saved, kind, registrations, tokens)
