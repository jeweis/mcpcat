"""旧日志阈值不依赖通用缓存 TTL；容量与端点生命周期明确。"""

import asyncio

import pytest
from fastmcp import Client, Context, FastMCP
from mcp.shared.exceptions import MCPError
from test_gateway_client_capabilities import live_http

from app.services.mcp_factory import MCPServerFactory
from app.services.proxy_logging import ProxyLogging


@pytest.mark.asyncio
async def test_legacy_level_change_applies_to_already_running_call():
    source = FastMCP("live level fixture")
    first, release = asyncio.Event(), asyncio.Event()
    received = []

    @source.tool()
    async def emit(ctx: Context) -> str:
        await ctx.warning("before")
        await release.wait()
        await ctx.warning("after-filtered")
        await ctx.log("after-visible", level="emergency")
        return "done"

    async def receive(message):
        received.append(message.level)
        first.set()

    async with asyncio.timeout(15), live_http(source.http_app(path="/mcp")) as target:
        proxy = MCPServerFactory.create_server(
            "live-level", {"type": "streamable-http", "url": target, "timeout": 5}
        )
        async with live_http(proxy.http_app(path="/mcp")) as url:
            async with Client(url, mode="legacy", log_handler=receive) as client:
                await client.set_logging_level("warning")
                call = asyncio.create_task(client.call_tool("emit"))
                try:
                    await first.wait()
                    await client.set_logging_level("emergency")
                    release.set()
                    assert (await call).data == "done"
                finally:
                    release.set()
                    if not call.done():
                        call.cancel()
                    await asyncio.gather(call, return_exceptions=True)
    assert received == ["warning", "emergency"]


@pytest.mark.asyncio
async def test_legacy_threshold_survives_state_expiry_and_clears_at_shutdown(
    monkeypatch,
):
    source = FastMCP("threshold fixture")

    @source.tool()
    async def emit(ctx: Context) -> str:
        await ctx.warning("filtered")
        await ctx.error("visible")
        return "done"

    messages = []

    async def receive(message):
        messages.append(message.level)

    original = Context.get_state

    async def expired(self, key):
        if key == "mcpcat:proxy-log-level":
            return None
        return await original(self, key)

    async with asyncio.timeout(15), live_http(source.http_app(path="/mcp")) as target:
        proxy = MCPServerFactory.create_server(
            "threshold", {"type": "streamable-http", "url": target, "timeout": 5}
        )
        policy = next(
            item for item in proxy.middleware if isinstance(item, ProxyLogging)
        )
        policy.max_sessions = 1
        async with live_http(proxy.http_app(path="/mcp")) as url:
            async with Client(url, mode="legacy", log_handler=receive) as client:
                await client.set_logging_level("error")
                monkeypatch.setattr(Context, "get_state", expired)
                assert (await client.call_tool("emit")).data == "done"
                assert messages == ["error"]
                # 容量满仍允许同一会话更新；新会话失败不驱逐已有阈值。
                await client.set_logging_level("emergency")
                async with Client(url, mode="legacy", log_handler=receive) as other:
                    with pytest.raises(MCPError, match="limit exceeded"):
                        await other.set_logging_level("debug")
                assert (await client.call_tool("emit")).data == "done"
                assert messages == ["error"]
                assert len(policy.levels) == 1
        assert policy.levels == {}


@pytest.mark.asyncio
async def test_failed_set_level_does_not_consume_capacity(monkeypatch):
    from types import SimpleNamespace
    from app.services import proxy_logging

    policy = ProxyLogging(max_sessions=1)
    ctx = SimpleNamespace(
        session_id="fixture-session",
        request_context=SimpleNamespace(protocol_version="2025-11-25"),
        fastmcp=SimpleNamespace(client_log_level="debug"),
    )
    monkeypatch.setattr(proxy_logging, "get_context", lambda: ctx)

    async def reject(context):
        raise MCPError(-32602, "invalid fixture level")

    with pytest.raises(MCPError, match="invalid fixture level"):
        await policy.on_request(
            SimpleNamespace(method="logging/setLevel", message={"level": "invalid"}),
            reject,
        )
    assert policy.levels == {}
    assert proxy_logging.current_log_level.get() is None
