"""真实代理工厂、回环 HTTP 上游与现代/legacy 普通客户端。"""

import asyncio
import json
from contextlib import AsyncExitStack

import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from test_skill_content_workflow import READ, WRITE, content_app

from app.application import create_app
from app.services.config_service import ConfigService
from app.services.inspector_service import inspector_service
from app.services.market_service import MarketService
from app.services.server_manager import MCPServerManager
from scripts.probe_fastmcp_network import serve_app


class ProtectedSource:
    """可信测试工具；不运行上传文件，只接受本来源固定凭证。"""

    def __init__(self, name: str) -> None:
        self.name, self.token = name, f"fixture-upstream-{name}"
        self.allowed = True
        self.seen, self.calls = [], []
        self.entered, self.cleaned = asyncio.Event(), asyncio.Event()
        self.server = FastMCP(name)
        self.server.tool(name="value")(self.value)
        self.app = self.server.http_app(path="/")

    async def value(self, value: int) -> str:
        """记录业务执行；特殊值用于在同一编排内撤销上游权限。"""
        self.calls.append(value)
        if value == 500:
            self.entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.cleaned.set()
        if value == -1:
            self.allowed = False
        return f"{self.name}:{value}"

    async def __call__(self, scope, receive, send) -> None:
        """保留 SDK 原生 lifespan，只在 HTTP 入口实施独立上游鉴权。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        self.seen.append(dict(headers))
        if not self.allowed or headers.get("authorization") != f"Bearer {self.token}":
            await JSONResponse({"detail": "fixture denied"}, status_code=403)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)


async def configured_gateway(stack, database, monkeypatch):
    """不替换 MCP 工厂：两来源同名工具、各自静态认证、临时网关。"""
    sources = {name: ProtectedSource(name) for name in ("alpha", "beta")}
    for name, source in sources.items():
        root = await stack.enter_async_context(serve_app(source))
        ConfigService.add_server_to_config(
            name,
            {
                "type": "streamable-http",
                "url": root + "/",
                "headers": {"Authorization": f"Bearer {source.token}"},
                "expose_in_catalog": True,
            },
        )
    ConfigService.update_setting_section("code_mode", {"enabled": True})
    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    app = create_app(database=database, server_manager=MCPServerManager())
    root = await stack.enter_async_context(serve_app(app))
    return root, app, sources


def connection(root, key, mode, path="code-mode"):
    """普通 SDK 客户端只向网关发送 mcpcat Key。"""
    return Client(
        StreamableHttpTransport(f"{root}/mcp/{path}/", headers={"Mcpcat-Key": key}),
        mode=mode,
        timeout=10,
    )


async def exercise_sources(client, sources):
    """搜索/Schema/同名路由和逐调用上游撤权，不借用其他来源凭证。"""
    found = await client.call_tool("search", {"query": "value"})
    assert all(f"{name}__value" in str(found) for name in sources)
    schema = await client.call_tool("get_schema", {"tools": ["alpha__value"]})
    assert "value" in str(schema)
    result = await client.call_tool(
        "execute",
        {
            "code": 'a = await call_tool("alpha__value", {"value": 2})\n'
            'b = await call_tool("beta__value", {"value": 3})\nreturn [a, b]'
        },
    )
    assert "alpha:2" in str(result) and "beta:3" in str(result)
    result = await client.call_tool(
        "execute",
        {
            "code": 'await call_tool("alpha__value", {"value": -1})\n'
            'return await call_tool("alpha__value", {"value": 999})'
        },
        raise_on_error=False,
    )
    assert result.is_error and "not rolled back" in str(result)
    assert sources["alpha"].calls == [2, -1]
    found = await client.call_tool("search", {"query": "value"})
    assert "alpha__value" not in str(found) and "beta__value" in str(found)
    result = await client.call_tool(
        "execute", {"code": 'return await call_tool("beta__value", {"value": 4})'}
    )
    assert "beta:4" in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_code_mode_real_upstreams_auth_and_name_isolation(
    content_app, monkeypatch, mode
):
    _, database = content_app
    async with AsyncExitStack() as stack:
        root, app, sources = await configured_gateway(stack, database, monkeypatch)
        async with connection(root, READ, mode) as client:
            await exercise_sources(client, sources)
            ConfigService.update_setting_section(
                "code_mode", {"enabled": True, "allowed_services": []}
            )
            hidden = await client.call_tool(
                "execute",
                {"code": 'return await call_tool("beta__value", {"value": 999})'},
                raise_on_error=False,
            )
            assert hidden.is_error and "tool_denied" in str(hidden)
            assert sources["beta"].calls == [3, 4]
        async with connection(root, WRITE, mode, path="mcpcat") as catalog:
            assert {tool.name for tool in await catalog.list_tools()} == {
                "search_tools",
                "call_tool",
            }
        for source in sources.values():
            assert source.seen
            assert all("mcpcat-key" not in headers for headers in source.seen)
            assert all(
                headers.get("authorization") == f"Bearer {source.token}"
                for headers in source.seen
            )
        assert not app.state.code_mode_runtime.bridge.active
        assert not app.state.code_mode_runtime.bridge.retained
        # 真正走过 HTTP 协议，而不是 mock Tool.run；没有把网关秘密放入资源。
        assert READ not in json.dumps([source.seen for source in sources.values()])


async def check_quota_cancel(root, mode, source, bridge):
    """两身份普通客户端并发：相同 Key 拒绝、不同 Key 可用、取消清理。"""
    async with connection(root, READ, mode) as reader, connection(
        root, WRITE, mode
    ) as writer:
        pending = asyncio.create_task(
            reader.call_tool(
                "execute",
                {"code": 'return await call_tool("alpha__value", {"value": 500})'},
            )
        )
        try:
            await asyncio.wait_for(source.entered.wait(), 5)
            denied = await reader.call_tool(
                "execute", {"code": "return 1"}, raise_on_error=False
            )
            assert denied.is_error and "principal_concurrency" in str(denied)
            other = await writer.call_tool("execute", {"code": "return 7"})
            assert json.loads(other.content[0].text) == 7
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await asyncio.wait_for(source.cleaned.wait(), 5)
        async with asyncio.timeout(5):
            while bridge.active or bridge.retained:
                await asyncio.sleep(0.01)
        result = await reader.call_tool("execute", {"code": "return 8"})
        assert json.loads(result.content[0].text) == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_native_clients_quota_cancel_and_call_limit(
    content_app, monkeypatch, mode
):
    _, database = content_app
    async with AsyncExitStack() as stack:
        root, app, sources = await configured_gateway(stack, database, monkeypatch)
        ConfigService.update_setting_section(
            "code_mode",
            {
                "enabled": True,
                "limits": {"principal_concurrency": 1, "max_tool_calls": 1},
            },
        )
        await check_quota_cancel(
            root, mode, sources["alpha"], app.state.code_mode_runtime.bridge
        )
        async with connection(root, READ, mode) as reader:
            result = await reader.call_tool(
                "execute",
                {
                    "code": 'await call_tool("beta__value", {"value": 1})\n'
                    'return await call_tool("beta__value", {"value": 2})'
                },
                raise_on_error=False,
            )
            assert result.is_error and "call_limit" in str(result)
            assert "completed 1/2" in str(result)
            assert sources["beta"].calls == [1]
