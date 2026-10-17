"""真实 Client 协商与 OAuth 公共发现 helper；全部使用本地内存传输。"""

import asyncio
import json

import httpx2
import pytest
from fastmcp import FastMCP
from starlette.responses import RedirectResponse
from test_proxy_cross_generation import single_era

from app.models.mcp_config import AuthStatus
from app.services.async_compat import operation_timeout
from app.services.oauth_flow import OAuthFlowService
from app.services.oauth_metadata import discover_oauth_endpoints
from app.services.oauth_probe import OAuthProbeError, probe_mcp_auth

URL = "https://resource.fixture/mcp"
ISSUER = "https://auth.fixture/tenant"
PRM = "https://metadata.fixture/resource"


def clients(monkeypatch, transport):
    original = httpx2.AsyncClient

    def create(*args, **kwargs):
        return original(*args, **kwargs, transport=transport)

    monkeypatch.setattr(httpx2, "AsyncClient", create)


def authorization_metadata(issuer=ISSUER):
    return {
        "issuer": issuer,
        "authorization_endpoint": "https://auth.fixture/authorize",
        "token_endpoint": "https://auth.fixture/token",
        "registration_endpoint": "https://auth.fixture/register",
        "code_challenge_methods_supported": ["S256"],
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
    }


def protected_metadata(resource=URL):
    return {
        "resource": resource,
        "authorization_servers": [ISSUER],
        "scopes_supported": ["read"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("era", ["modern", "legacy"])
@pytest.mark.parametrize("redirect", [False, True])
async def test_actual_client_negotiation_no_auth(monkeypatch, era, redirect):
    source = FastMCP("oauth-probe")
    app = source.http_app(path="/mcp/" if redirect else "/mcp", json_response=True)
    methods = []
    upstream = single_era(app, era, methods)

    async def application(scope, receive, send):
        if redirect and scope.get("path") == "/mcp":
            await RedirectResponse("/mcp/", status_code=307)(scope, receive, send)
        else:
            await upstream(scope, receive, send)

    clients(monkeypatch, httpx2.ASGITransport(app=application))
    async with operation_timeout(5), app.lifespan(app):
        result = await probe_mcp_auth(URL)
    assert result.challenge is None
    assert result.protocol_version == (
        "2026-07-28" if era == "modern" else "2025-11-25"
    )
    assert "server/discover" in methods
    assert ("initialize" in methods) is (era == "legacy")
    assert not any(method.startswith("tools/") for method in methods)


@pytest.mark.asyncio
@pytest.mark.parametrize("server_type", ["streamable-http", "sse"])
async def test_challenge_prm_separate_issuer_no_credential_forwarding(
    monkeypatch, server_type
):
    requests = []

    def handler(request):
        requests.append(request)
        if str(request.url) == URL:
            assert request.headers["X-Upstream"] == "fixture-secret"
            if server_type == "sse":
                assert request.method == "GET"
            else:
                assert json.loads(request.content)["method"] == "server/discover"
            return httpx2.Response(
                401, headers={"WWW-Authenticate": f'Bearer resource_metadata="{PRM}"'}
            )
        assert "X-Upstream" not in request.headers
        assert "Authorization" not in request.headers
        assert "Mcpcat-Key" not in request.headers
        if str(request.url) == PRM:
            return httpx2.Response(200, json=protected_metadata())
        if (
            str(request.url)
            == "https://auth.fixture/.well-known/oauth-authorization-server/tenant"
        ):
            return httpx2.Response(200, json=authorization_metadata())
        return httpx2.Response(404)

    clients(monkeypatch, httpx2.MockTransport(handler))
    status, endpoints = await OAuthFlowService.inspect_auth_requirement(
        URL, {"X-Upstream": "fixture-secret"}, server_type
    )
    assert status == AuthStatus.AUTH_REQUIRED
    assert endpoints["issuer"] == ISSUER
    assert endpoints["resource"] == URL
    assert endpoints["scopes_supported"] == ["read"]
    assert endpoints["client_id_metadata_document_supported"] is True
    assert len(requests) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-protected-resource",
    ],
)
async def test_resource_well_known_and_oidc_fallback(monkeypatch, path):
    visited = []

    def handler(request):
        visited.append(str(request.url))
        if request.url.host == "resource.fixture" and request.url.path == path:
            return httpx2.Response(200, json=protected_metadata())
        if request.url.path == "/.well-known/openid-configuration/tenant":
            return httpx2.Response(200, json=authorization_metadata())
        return httpx2.Response(404)

    clients(monkeypatch, httpx2.MockTransport(handler))
    result = await discover_oauth_endpoints(URL)
    assert result["issuer"] == ISSUER
    assert visited[-1].endswith("/.well-known/openid-configuration/tenant")


@pytest.mark.asyncio
async def test_legacy_root_discovery(monkeypatch):
    def handler(request):
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx2.Response(
                200, json=authorization_metadata("https://resource.fixture")
            )
        return httpx2.Response(404)

    clients(monkeypatch, httpx2.MockTransport(handler))
    endpoints = await discover_oauth_endpoints(URL)
    assert endpoints["issuer"] == "https://resource.fixture"
    assert endpoints["token_endpoint"] == "https://auth.fixture/token"


@pytest.mark.asyncio
async def test_401_without_oauth_is_unsupported_not_none(monkeypatch):
    clients(
        monkeypatch,
        httpx2.MockTransport(
            lambda request: httpx2.Response(401 if str(request.url) == URL else 404)
        ),
    )
    assert (
        await OAuthFlowService.detect_auth_requirement(URL)
        == AuthStatus.AUTH_UNSUPPORTED
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 403, 404, 500])
async def test_http_failure_not_none_or_auth(monkeypatch, status):
    clients(monkeypatch, httpx2.MockTransport(lambda request: httpx2.Response(status)))
    with pytest.raises(OAuthProbeError) as raised:
        await probe_mcp_auth(URL)
    assert raised.value.category == "http"
    assert "resource.fixture" not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception,category",
    [(httpx2.ConnectError, "connection"), (httpx2.ReadTimeout, "timeout")],
)
async def test_network_failure_not_none_or_legacy_verdict(
    monkeypatch, exception, category
):
    def handler(request):
        raise exception("secret-in-upstream-message", request=request)

    clients(monkeypatch, httpx2.MockTransport(handler))
    with pytest.raises(OAuthProbeError) as raised:
        await probe_mcp_auth(URL)
    assert raised.value.category == category
    assert "secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_disjoint_protocol_is_not_auth_or_no_auth(monkeypatch):
    methods = []

    def handler(request):
        body = json.loads(request.content)
        methods.append(body["method"])
        return httpx2.Response(
            400,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "error": {
                    "code": -32022,
                    "message": "unsupported",
                    "data": {"supported": ["2099-01-01"], "requested": "2026-07-28"},
                },
            },
        )

    clients(monkeypatch, httpx2.MockTransport(handler))
    with pytest.raises(OAuthProbeError) as raised:
        await probe_mcp_auth(URL)
    assert raised.value.category == "protocol_version"
    assert methods == ["server/discover"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["issuer", "resource", "oversize", "url", "server_error"]
)
async def test_invalid_metadata_fails_closed_without_legacy_downgrade(
    monkeypatch, kind
):
    visited = []

    def handler(request):
        visited.append(str(request.url))
        if request.url.path.startswith("/.well-known/oauth-protected-resource"):
            if kind == "oversize":
                return httpx2.Response(200, content=b"x" * (256 * 1024 + 1))
            if kind == "server_error":
                return httpx2.Response(503)
            data = protected_metadata(
                "https://wrong.fixture" if kind == "resource" else URL
            )
            if kind == "url":
                data["authorization_servers"] = ["http://auth.fixture/insecure"]
            return httpx2.Response(200, json=data)
        return httpx2.Response(
            200,
            json=authorization_metadata(
                "https://wrong.fixture" if kind == "issuer" else ISSUER
            ),
        )

    clients(monkeypatch, httpx2.MockTransport(handler))
    with pytest.raises(OAuthProbeError):
        await discover_oauth_endpoints(URL)
    assert (
        "https://resource.fixture/.well-known/oauth-authorization-server" not in visited
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata_url",
    [
        "file:///etc/invalid",
        "http://private.fixture/prm",
        "https://user:fixture-secret@metadata.fixture/prm",
        "https://metadata.fixture:99999/prm",
        "https://metadata.fixture/prm#fragment",
    ],
)
async def test_unsafe_advertised_metadata_url_never_requested(
    monkeypatch, metadata_url
):
    requested = []

    def handler(request):
        requested.append(request)
        return httpx2.Response(404)

    clients(monkeypatch, httpx2.MockTransport(handler))
    challenge = httpx2.Response(
        401, headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata_url}"'}
    )
    with pytest.raises(OAuthProbeError) as raised:
        await discover_oauth_endpoints(URL, challenge)
    assert not requested
    assert "fixture-secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_discovery_redirect_not_followed(monkeypatch):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        return httpx2.Response(
            302, headers={"Location": "https://redirect.fixture/leak"}
        )

    clients(monkeypatch, httpx2.MockTransport(handler))
    assert await discover_oauth_endpoints(URL) is None
    assert not any("redirect.fixture" in url for url in requested)


@pytest.mark.asyncio
async def test_invalid_target_is_safe_protocol_error():
    with pytest.raises(OAuthProbeError) as raised:
        await probe_mcp_auth("file:///fixture-secret")
    assert raised.value.category == "protocol"
    assert "fixture-secret" not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        "https://redirect.fixture/mcp",
        "https://user:fixture-secret@resource.fixture/mcp/",
    ],
)
async def test_probe_redirect_never_sends_configured_headers_elsewhere(
    monkeypatch, target
):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        assert request.headers["X-Upstream"] == "fixture-secret"
        return httpx2.Response(307, headers={"Location": target})

    clients(monkeypatch, httpx2.MockTransport(handler))
    with pytest.raises(OAuthProbeError) as raised:
        await probe_mcp_auth(URL, {"X-Upstream": "fixture-secret"})
    assert raised.value.category == "redirect"
    assert requested == [URL]
    assert "fixture-secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_cancelled_probe_does_not_return_none(monkeypatch):
    started = asyncio.Event()

    async def handler(request):
        started.set()
        await asyncio.Event().wait()

    clients(monkeypatch, httpx2.MockTransport(handler))
    task = asyncio.create_task(probe_mcp_auth(URL))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
