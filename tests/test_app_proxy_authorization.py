"""真实 MCP HTTP 序列化经内存 ASGI 上游，验证配置认证不串入站 Key。"""

import asyncio

import httpx2
import pytest
from fastmcp import FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from test_app_tool_bindings import bound_app, prepare, publish
from test_catalog import server_info
from test_skill_content_workflow import READ, content_app
from test_skills_mcp_gateway import rpc

from app.services import proxy_clients
from app.services.config_service import ConfigService
from app.services.mcp_factory import MCPServerFactory


def upstream_fixture():
    """上游只返回夹具数据；403 模拟现有来源权限被收回。"""
    source = FastMCP("authorized-upstream")
    evidence = {"headers": [], "calls": [], "denied": False}

    @source.tool()
    def lookup(value: int) -> dict:
        evidence["calls"].append(value)
        return {"source": "http-upstream", "value": value}

    http_app = source.http_app(path="/mcp", json_response=True)

    async def application(scope, receive, send):
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            evidence["headers"].append(dict(headers))
            if (
                evidence["denied"]
                or headers.get("authorization") != "Bearer upstream-only-fixture"
            ):
                await JSONResponse({"detail": "forbidden"}, status_code=403)(
                    scope, receive, send
                )
                return
        await http_app(scope, receive, send)

    return http_app, application, evidence


def configure_proxy(app, monkeypatch, upstream, oauth):
    """只替换公开 HTTP client factory；真实代理与认证对象保持不变。"""

    def transport(**kwargs):
        return StreamableHttpTransport(
            **kwargs,
            httpx_client_factory=lambda **options: httpx2.AsyncClient(
                **options, transport=httpx2.ASGITransport(app=upstream)
            ),
        )

    monkeypatch.setattr(proxy_clients, "StreamableHttpTransport", transport)
    config = {"type": "streamable-http", "url": "http://upstream/mcp", "timeout": 3}
    if oauth:
        config["oauth"] = {"token": {"access_token": "upstream-only-fixture"}}
    else:
        config["headers"] = {"Authorization": "Bearer upstream-only-fixture"}
    ConfigService.update_server_config("a", config)
    app.state.server_manager.server_info["a"] = server_info(
        MCPServerFactory.create_server("a", config)
    )


async def invoke_callbacks(client, evidence):
    """从 Apps HTTP 入口调用，入站身份确实存在于 HTTP 请求上下文。"""
    tools = (await rpc(client, "tools/list", path="apps")).json()["result"]["tools"]
    name = next(tool["name"] for tool in tools if tool.get("title") == "first")
    result = await rpc(
        client,
        "tools/call",
        {
            "name": name,
            "arguments": {"value": 7},
        },
        path="apps",
    )
    assert result.json()["result"]["structuredContent"] == {
        "source": "http-upstream",
        "value": 7,
    }
    evidence["denied"] = True
    async with asyncio.timeout(5):
        rejected = await rpc(
            client,
            "tools/call",
            {
                "name": name,
                "arguments": {"value": 9},
            },
            path="apps",
        )
    assert rejected.json()["result"]["isError"]
    assert "upstream-only-fixture" not in rejected.text


@pytest.mark.asyncio
@pytest.mark.parametrize("oauth", [False, True])
async def test_app_source_uses_configured_auth_and_rechecks_upstream_denial(
    bound_app, monkeypatch, oauth
):
    app, database, _ = bound_app
    http_app, upstream, evidence = upstream_fixture()
    configure_proxy(app, monkeypatch, upstream, oauth)
    ConfigService.update_setting_section("apps", {"enabled": True})
    runtime = app.state.apps_mcp_runtime
    for name in app.state.server_manager.server_info:
        runtime.register_source(name)
    async with http_app.lifespan(http_app), runtime.lifespan(), httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        await prepare(client)
        assert (await publish(client)).status_code == 200
        await invoke_callbacks(client, evidence)
    assert evidence["calls"] == [7]
    assert evidence["headers"]
    assert all(
        row.get("authorization") == "Bearer upstream-only-fixture"
        for row in evidence["headers"]
    )
    assert all("mcpcat-key" not in row for row in evidence["headers"])
    assert READ not in str(evidence["headers"])
