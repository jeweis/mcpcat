"""FastMCP 4 代理客户端隔离及公开兼容模式测试。"""

import sys
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from fastmcp import Client, FastMCP

from app.services import proxy_clients
from app.services.inspector_service import (
    InspectorService,
    InspectorSession,
    McpcatAuth,
)
from app.services.mcp_factory import MCPServerFactory


@pytest.mark.parametrize("server_type", ["stdio", "sse", "streamable-http"])
def test_factory_creates_isolated_clients(monkeypatch, server_type):
    """每次调用使用独立连接，禁止入站 Key 自动传上游。"""
    created = []

    def capture(transport, **kwargs):
        instance = SimpleNamespace(transport=transport, **kwargs)
        created.append(instance)
        return instance

    monkeypatch.setattr(proxy_clients, "ProxyClient", capture)
    config = {
        "type": server_type,
        "url": "https://upstream.invalid/mcp",
        "command": "unused",
        "args": [],
        "headers": {"X-Upstream": "fixture"},
        "timeout": 17,
    }
    factory = proxy_clients.make_proxy_client_factory(config)
    first, second = factory(), factory()
    assert first is not second
    assert first.forward_incoming_headers is False
    assert first.timeout == 17
    if server_type != "stdio":
        assert first.transport is not second.transport


@pytest.mark.parametrize(
    "server_type, expected",
    [
        ("stdio", "auto"),
        ("sse", "legacy"),
        ("streamable-http", "auto"),
    ],
)
def test_mode_uses_public_negotiation(server_type, expected):
    """不从下游请求时代推断上游，SSE 本身仍使用 legacy。"""
    assert proxy_clients.upstream_mode(server_type) == expected


def test_inspector_auth_uses_httpx2():
    """Inspector auth 对象符合升级后的 HTTP 类型要求。"""
    auth = McpcatAuth("test-only-key")
    assert isinstance(auth, httpx2.Auth)
    request = next(auth.auth_flow(httpx2.Request("GET", "https://fixture.invalid")))
    assert request.headers["Mcpcat-Key"] == "test-only-key"


def test_inspector_auth_retains_custom_header(monkeypatch):
    """已有实例修改过认证头时仍使用该配置。"""
    from app.services.inspector_service import security_service

    monkeypatch.setattr(
        security_service, "get_auth_header_name", lambda: "X-Custom-Key"
    )
    request = next(
        McpcatAuth("test-only-key").auth_flow(
            httpx2.Request("GET", "https://fixture.invalid"),
        )
    )
    assert request.headers["X-Custom-Key"] == "test-only-key"
    assert "Mcpcat-Key" not in request.headers


@pytest.mark.asyncio
async def test_inspector_schema_and_result_contract():
    """Inspector 应用级 session 与旧 REST 字段不依赖协议 session id。"""
    server = FastMCP("Inspector fixture")

    @server.tool()
    def lookup(topic: str) -> dict:
        """Lookup a topic."""
        return {"topic": topic}

    service = InspectorService()
    session = InspectorSession("fixture", Client(server))
    service.sessions[session.id] = session
    tools = await service.get_tools(session.id)
    assert tools[0]["input_schema"]["properties"]["topic"]["type"] == "string"
    result = await service.call_tool(session.id, "lookup", {"topic": "test"})
    assert result["data"] == {"topic": "test"}
    assert {"data", "content", "is_error"} <= set(result)
    assert result["structured_content"] == {"topic": "test"}
    assert result["protocol_content"][0]["type"] == "text"
    assert result["is_error"] is False
    await service.close_session(session.id)
    assert session.id not in service.sessions


@pytest.mark.asyncio
async def test_inspector_known_http_route_does_not_infer_sse_from_service_name(
    monkeypatch,
):
    """服务名 sse 不能把已知的 /mcp/{name} HTTP 路由变成旧 SSE。"""
    from fastmcp.client.transports import StreamableHttpTransport

    from app.services import inspector_service as module

    captured = []

    class FixtureClient:
        def __init__(self, transport, **kwargs):
            captured.append(transport)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def list_tools(self):
            return []

    monkeypatch.setattr(module, "Client", FixtureClient)
    service = InspectorService()
    sid = await service.create_session("sse", "http://127.0.0.1:12345")
    assert isinstance(captured[0], StreamableHttpTransport)
    assert captured[0].url == "http://127.0.0.1:12345/mcp/sse"
    await service.close_session(sid)


@pytest.mark.asyncio
async def test_openapi_uses_httpx2_and_retains_route_map(monkeypatch):
    """真实 OpenAPI Provider 使用新 HTTP 类型，并保留白名单路由。"""
    from app.services import mcp_factory

    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Fixture", "version": "1"},
        "paths": {
            "/items": {
                "get": {
                    "operationId": "list_items",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "additionalProperties": True,
                                    }
                                }
                            },
                        },
                    },
                }
            }
        },
    }
    original_client = httpx2.AsyncClient
    monkeypatch.setattr(
        mcp_factory.httpx2, "get", lambda url: SimpleNamespace(json=lambda: spec)
    )
    monkeypatch.setattr(
        mcp_factory.httpx2,
        "AsyncClient",
        lambda **kwargs: original_client(
            **kwargs,
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(
                    200,
                    json={"items": ["fixture"]},
                )
            ),
        ),
    )
    server = MCPServerFactory._create_openapi_server(
        {
            "api_base_url": "https://fixture.invalid",
            "spec_url": "https://fixture.invalid/spec",
            "route_configs": [{"methods": ["GET"], "pattern": ".*"}],
        }
    )
    async with Client(server) as client:
        tools = await client.list_tools()
        assert len(tools) == 1
        result = await client.call_tool(tools[0].name, {})
        assert result.data == {"items": ["fixture"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_stdio_factory_real_protocol(mode):
    """真实子进程和两个客户端时代，覆盖代理工厂而不是替身。"""
    fixture = Path(__file__).parent / "fixtures" / "mcp_stdio_probe.py"
    proxy = MCPServerFactory.create_server(
        "fixture",
        {
            "type": "stdio",
            "command": sys.executable,
            "args": [str(fixture)],
            "env": {},
            "timeout": 10,
        },
    )
    async with Client(proxy, mode=mode) as client:
        tools = await client.list_tools()
        echo = next(tool for tool in tools if tool.name.endswith("echo"))
        result = await client.call_tool(echo.name, {"value": "passed"})
        assert result.data == "passed"


def test_runtime_preserves_old_idle_default_and_explicit_timeout(monkeypatch):
    """通过框架公开设置控制旧会话，不访问 session manager 私有对象。"""
    import fastmcp

    from app.core.config import settings
    from app.core.mcp_runtime import configure_mcp_runtime

    monkeypatch.setattr(fastmcp.settings, "http_session_idle_timeout", "auto")
    monkeypatch.setattr(settings, "mcpcat_mcp_http_session_idle_timeout", 0)
    configure_mcp_runtime()
    assert fastmcp.settings.http_session_idle_timeout is None
    monkeypatch.setattr(settings, "mcpcat_mcp_http_session_idle_timeout", 120)
    configure_mcp_runtime()
    assert fastmcp.settings.http_session_idle_timeout == 120


@pytest.mark.parametrize("server_type", ["stdio", "sse", "streamable-http"])
def test_factory_retains_existing_configuration(server_type):
    """工厂仍接受原三类配置，创建过程不启动真实上游。"""
    proxy = MCPServerFactory.create_server(
        "fixture",
        {
            "type": server_type,
            "command": "unused",
            "args": [],
            "url": "https://fixture.invalid/mcp",
            "headers": {},
        },
    )
    assert proxy is not None
