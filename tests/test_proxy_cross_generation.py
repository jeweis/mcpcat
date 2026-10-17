"""上下游独立时代：夹具明确拒绝另一代协商，而非双栈服务假阳性。"""

import json

import pytest
from fastmcp import Client, Context, FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse
from test_upstream_apps_proxy import proxy_targets

from app.services.async_compat import operation_timeout


def single_era(app, era: str, evidence: list):
    """只在测试 HTTP 边界拒绝另一代发现/握手，不修改 SDK dispatcher。"""

    async def application(scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await app(scope, receive, send)
        body = await Request(scope, receive).body()
        message = json.loads(body)
        method = message.get("method")
        evidence.append(method)
        if (era == "modern" and method == "initialize") or (
            era == "legacy" and method == "server/discover"
        ):
            await JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {
                        "code": -32601,
                        "message": "Unsupported negotiation era",
                    },
                }
            )(scope, receive, send)
            return
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await app(scope, replay, send)

    return application


def source_fixture() -> FastMCP:
    """业务返回实际协商时代，不能靠测试 mode 名称证明兼容。"""
    source = FastMCP("single-era-source")

    @source.tool()
    def echo(value: str, ctx: Context) -> dict:
        return {"value": value, "protocol": ctx.request_context.protocol_version}

    @source.resource("fixture://protocol")
    def protocol(ctx: Context) -> str:
        return ctx.request_context.protocol_version

    return source


@pytest.mark.asyncio
@pytest.mark.parametrize("era", ["modern", "legacy"])
@pytest.mark.parametrize("downstream", ["auto", "legacy"])
async def test_proxy_independently_negotiates_single_era_upstream(
    monkeypatch, era, downstream
):
    source = source_fixture()
    http = source.http_app(path="/mcp", json_response=True)
    evidence = []
    target = "http://single-era.fixture/mcp"
    proxy = proxy_targets(monkeypatch, {target: single_era(http, era, evidence)})[
        target
    ]
    expected = "2026-07-28" if era == "modern" else "2025-11-25"
    async with operation_timeout(10), http.lifespan(http), Client(
        proxy, mode=downstream
    ) as client:
        result = await client.call_tool("echo", {"value": "passed"})
        assert result.data == {"value": "passed", "protocol": expected}
        assert (await client.read_resource("fixture://protocol"))[0].text == expected
        assert client.protocol_version == (
            "2026-07-28" if downstream == "auto" else "2025-11-25"
        )
    assert "server/discover" in evidence
    if era == "modern":
        assert "initialize" not in evidence
    else:
        assert "initialize" in evidence
