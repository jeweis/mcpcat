"""机器认证的真实 HTTP 流验证，不用缓冲式 ASGITransport 代替 SSE。"""

import pytest
from fastmcp import Context, FastMCP
from starlette.types import ASGIApp
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.services.proxy_clients import make_proxy_client_factory
from app.services.async_compat import operation_timeout
from tests.test_gateway_client_capabilities import live_http
from tests.test_proxy_cross_generation import single_era


def authenticated_source(app: ASGIApp, evidence: list[str]) -> ASGIApp:
    """合成授权服务器与 MCP 来源；所有请求仍经真实 TCP/HTTP。"""

    async def application(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        request = Request(scope, receive)
        origin = str(request.base_url).rstrip("/")
        path = request.url.path
        if path == "/metadata":
            response = JSONResponse(
                {
                    "resource": origin + "/mcp",
                    "authorization_servers": [origin],
                    "scopes_supported": ["read"],
                }
            )
        elif path == "/.well-known/oauth-authorization-server":
            response = JSONResponse(
                {
                    "issuer": origin,
                    "token_endpoint": origin + "/token",
                    "authorization_endpoint": origin + "/authorize",
                    "grant_types_supported": ["client_credentials"],
                    "token_endpoint_auth_methods_supported": ["client_secret_basic"],
                }
            )
        elif path == "/token":
            assert request.headers.get("authorization", "").startswith("Basic ")
            assert b"grant_type=client_credentials" in await request.body()
            evidence.append("token")
            response = JSONResponse(
                {
                    "access_token": "network-fixture-token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                }
            )
        elif request.headers.get("authorization") == "Bearer network-fixture-token":
            evidence.append(request.method + " " + path)
            return await app(scope, receive, send)
        else:
            response = JSONResponse(
                {},
                status_code=401,
                headers={
                    "WWW-Authenticate": f'Bearer resource_metadata="{origin}/metadata"',
                },
            )
        await response(scope, receive, send)

    return application


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["modern", "legacy", "sse"])
async def test_machine_auth_streaming_network(transport: str) -> None:
    """新旧 HTTP 和 SSE 都必须完成实际协商、列表及业务调用。"""
    source = FastMCP("machine-network")

    @source.tool()
    async def echo(value: str, ctx: Context) -> dict:
        return {"value": value, "protocol": ctx.request_context.protocol_version}

    app = source.http_app(
        path="/mcp", transport=("sse" if transport == "sse" else "streamable-http")
    )
    negotiation, evidence = [], []
    if transport != "sse":
        app = single_era(app, transport, negotiation)
    async with operation_timeout(20), live_http(
        authenticated_source(app, evidence)
    ) as url:
        origin = url.removesuffix("/mcp")
        factory = make_proxy_client_factory(
            {
                "type": "sse" if transport == "sse" else "streamable-http",
                "url": url,
                "timeout": 5,
                "oauth": {
                    "grant_type": "client_credentials",
                    "issuer": origin,
                    "token_endpoint": origin + "/token",
                    "client_id": "fixture",
                    "client_secret": "synthetic-secret",
                    "scopes": ["read"],
                },
            }
        )
        async with factory() as client:
            assert [tool.name for tool in await client.list_tools()] == ["echo"]
            result = await client.call_tool("echo", {"value": "network"})
            expected = "2026-07-28" if transport == "modern" else "2025-11-25"
            assert result.data == {"value": "network", "protocol": expected}
            assert client.protocol_version == expected
    assert evidence.count("token") == 1
    if transport != "modern":
        assert "GET /mcp" in evidence
    if transport != "sse":
        assert ("initialize" in negotiation) == (transport == "legacy")
