"""Tasks 适配不改变旧客户端、普通结果与平台本地工具分发。"""

import asyncio

import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp_tasks import TasksExtension
from fastmcp.server.providers.proxy import FastMCPProxy
from fastmcp.exceptions import ToolError
from fastmcp.utilities.tasks import TaskConfig
from mcp.shared.exceptions import MCPError
from test_gateway_client_capabilities import live_http

from app.services.proxy_clients import make_proxy_client_factory
from app.services.proxy_tasks import ProxyTasks


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_ordinary_and_local_tools_are_not_replayed_or_redirected(mode):
    source = FastMCP("non-task source")
    calls = []

    @source.tool()
    async def ordinary(value: int, ctx: Context) -> dict:
        calls.append(value)
        await ctx.report_progress(1, 2, "fixture-progress")
        return {"received": value}

    async with asyncio.timeout(30), live_http(source.http_app(path="/mcp")) as upstream:
        factory = make_proxy_client_factory(
            {"type": "streamable-http", "url": upstream}
        )
        gateway = FastMCPProxy(client_factory=factory)
        adapter = ProxyTasks(factory, lambda: "fixture", lambda: True)
        gateway.add_extension(adapter)

        @gateway.tool()
        async def platform_local() -> str:
            return "platform-local-result"

        async with live_http(gateway.http_app(path="/mcp")) as endpoint:
            progress = []

            async def on_progress(current, total, message):
                progress.append((current, total, message))

            async with Client(
                endpoint, mode=mode, progress_handler=on_progress
            ) as client:
                assert (
                    await client.call_tool("ordinary", {"value": 7})
                ).structured_content == {"received": 7}
                assert "platform-local-result" in str(
                    await client.call_tool("platform_local")
                )
                assert calls == [7]
                assert progress == [(1, 2, "fixture-progress")]
                assert adapter.bindings == {}


@pytest.mark.asyncio
async def test_explicit_local_version_is_not_routed_to_upstream_version():
    source = FastMCP("version source")
    source.add_extension(TasksExtension(url="memory://", name="version-source"))

    @source.tool(name="versioned", version="2", task=True)
    async def upstream_version() -> str:
        return "upstream-v2"

    async with asyncio.timeout(30), live_http(source.http_app(path="/mcp")) as upstream:
        factory = make_proxy_client_factory(
            {"type": "streamable-http", "url": upstream}
        )
        gateway = FastMCPProxy(client_factory=factory)
        adapter = ProxyTasks(factory, lambda: "fixture", lambda: True)
        gateway.add_extension(adapter)

        @gateway.tool(name="versioned", version="1")
        async def local_version() -> str:
            return "local-v1"

        async with live_http(gateway.http_app(path="/mcp")) as endpoint:
            async with Client(endpoint) as client:
                assert "local-v1" in str(
                    await client.call_tool("versioned", version="1")
                )
                assert adapter.bindings == {}
                assert "upstream-v2" in str(
                    await client.call_tool("versioned", version="2")
                )
                assert len(adapter.bindings) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["tool", "resource", "prompt"])
@pytest.mark.parametrize("task_mode", ["optional", "required"])
async def test_nested_tool_call_does_not_inherit_task_opt_in(entrypoint, task_mode):
    source = FastMCP("nested source")
    source.add_extension(TasksExtension(url="memory://", name="nested-source"))
    calls = []

    @source.tool(task=TaskConfig(mode=task_mode))
    async def inner() -> str:
        calls.append("executed")
        return "inline-inner-result"

    async with asyncio.timeout(30), live_http(source.http_app(path="/mcp")) as upstream:
        factory = make_proxy_client_factory(
            {"type": "streamable-http", "url": upstream}
        )
        gateway = FastMCPProxy(client_factory=factory)
        adapter = ProxyTasks(factory, lambda: "fixture", lambda: True)
        gateway.add_extension(adapter)

        @gateway.tool()
        async def outer(ctx: Context) -> str:
            result = await ctx.fastmcp.call_tool("inner", {})
            assert adapter.bindings == {}, "内部调用不能创建代理任务"
            return str(result)

        gateway.resource("resource://nested")(outer)
        gateway.prompt()(outer)

        async with live_http(gateway.http_app(path="/mcp")) as endpoint:
            async with Client(endpoint) as client:
                operation = {
                    "tool": lambda: client.call_tool("outer"),
                    "resource": lambda: client.read_resource("resource://nested"),
                    "prompt": lambda: client.get_prompt("outer"),
                }[entrypoint]
                if task_mode == "required":
                    with pytest.raises((MCPError, ToolError)):
                        await operation()
                    assert calls == []
                else:
                    assert "inline-inner-result" in str(await operation())
                    assert calls == ["executed"]
                assert adapter.bindings == {}
