"""HTTP 两跳真实协议序列化：业务 metadata 保留，连接身份/头不串源。"""

import asyncio
import json

import httpx2
import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.prompts import PromptResult
from fastmcp.server.dependencies import get_http_request
from mcp_types import Implementation
from starlette.datastructures import Headers
from test_upstream_apps_proxy import proxy_targets


def source_fixture() -> FastMCP:
    """只返回此测试请求的非生产协议证据，没有真实凭证。"""
    source = FastMCP("metadata-upstream")

    @source.tool()
    def inspect_request(ctx: Context) -> dict:
        return request_evidence(ctx)

    @source.resource("evidence://request", mime_type="application/json")
    def resource_request(ctx: Context) -> str:
        return json.dumps(request_evidence(ctx))

    @source.prompt()
    def prompt_request(ctx: Context) -> PromptResult:
        return PromptResult(json.dumps(request_evidence(ctx)))

    return source


def request_evidence(ctx: Context) -> dict:
    """只返回测试上下文，借公开 API 观察元数据和两跳请求头。"""
    request = ctx.request_context
    headers = get_http_request().headers
    keys = (
        "mcpcat-key",
        "x-inbound-private",
        "x-configured",
        "mcp-method",
        "mcp-protocol-version",
    )
    return {
        "protocol": request.protocol_version,
        "meta": dict(request.meta or {}),
        "headers": {key: headers[key] for key in keys if key in headers},
    }


async def probe_methods(client: Client, mode: str) -> None:
    """三类方法各自经过代理处理路径，不能只证明 tools/call。"""
    metadata = {"fixture/request": {"trace": [1, "kept"]}}
    tool = await client.call_tool("inspect_request", {}, meta=metadata)
    resource = await client.read_resource_mcp("evidence://request", meta=metadata)
    prompt = await client.get_prompt_mcp("prompt_request", {}, meta=metadata)
    for method, data in [
        ("tools/call", tool.data),
        ("resources/read", json.loads(resource.contents[0].text)),
        ("prompts/get", json.loads(prompt.messages[0].content.text)),
    ]:
        assert data["meta"]["fixture/request"] == metadata["fixture/request"]
        assert "downstream-only-fixture" not in str(data["meta"])
        headers = data["headers"]
        assert headers["x-configured"] == "upstream-fixture"
        assert "mcpcat-key" not in headers and "x-inbound-private" not in headers
        assert data["protocol"] == "2026-07-28"
        assert headers["mcp-protocol-version"] == data["protocol"]
        assert headers["mcp-method"] == method


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_hop_safe_metadata_and_configured_only_headers(monkeypatch, mode):
    source = source_fixture()
    upstream = source.http_app(path="/mcp", json_response=True)
    target = "http://upstream.fixture/mcp"
    proxy = proxy_targets(
        monkeypatch, {target: upstream}, {"X-Configured": "upstream-fixture"}
    )[target]
    frontend = proxy.http_app(path="/mcp", json_response=True)
    inbound = []

    async def application(scope, receive, send):
        if scope["type"] == "http":
            inbound.append(dict(Headers(scope=scope)))
        await frontend(scope, receive, send)

    transport = StreamableHttpTransport(
        "http://gateway.fixture/mcp",
        headers={
            "Mcpcat-Key": "inbound-key-fixture",
            "X-Inbound-Private": "private-fixture",
        },
        httpx_client_factory=lambda **options: httpx2.AsyncClient(
            **options, transport=httpx2.ASGITransport(app=application)
        ),
    )
    async with asyncio.timeout(10), upstream.lifespan(upstream), frontend.lifespan(
        frontend
    ):
        async with Client(
            transport,
            mode=mode,
            client_info=Implementation(name="downstream-only-fixture", version="1"),
        ) as client:
            await probe_methods(client, mode)
    assert any(row.get("mcpcat-key") == "inbound-key-fixture" for row in inbound)
