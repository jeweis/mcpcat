"""真实旧协议目录通知按客户端会话隔离，操作退出后不留下路由。"""

import asyncio

import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.server.middleware import Middleware
from test_gateway_client_capabilities import live_http
from test_proxy_cross_generation import single_era

from app.services.mcp_factory import MCPServerFactory
from app.services.roots_notifications import RootsNotifications


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
@pytest.mark.parametrize("ending", ["finish", "cancel", "revoke"])
async def test_roots_changes_reach_only_current_client_and_cleanup(transport, ending):
    waiting, received = {}, []
    both_ready = asyncio.Event()
    owners_ready = set()

    class Observe(Middleware):
        async def on_notification(self, context, call_next):
            if context.method == "notifications/roots/list_changed":
                sid = context.fastmcp_context.session_id
                received.append(sid)
                waiting[sid].set()
            return await call_next(context)

    source = FastMCP("roots fixture", middleware=[Observe()])

    @source.tool()
    async def watch(ctx: Context) -> dict:
        assert ctx.session.client_capabilities.roots.list_changed is True
        first = await ctx.session.list_roots()
        event = asyncio.Event()
        sid = ctx.session_id
        waiting[sid] = event
        try:
            await ctx.report_progress(0, total=1)
            await event.wait()
            second = await ctx.session.list_roots()
            return {
                "before": [str(root.uri) for root in first.roots],
                "after": [str(root.uri) for root in second.roots],
            }
        finally:
            waiting.pop(sid, None)

    app = source.http_app(path="/mcp", transport=transport, json_response=False)
    application = single_era(app, "legacy", []) if transport != "sse" else app
    async with asyncio.timeout(30), live_http(application) as upstream:
        proxy = MCPServerFactory.create_server(
            "roots", {"type": transport, "url": upstream, "timeout": 15}
        )
        relay = next(
            item for item in proxy.middleware if isinstance(item, RootsNotifications)
        )
        async with live_http(proxy.http_app(path="/mcp", json_response=False)) as url:

            async def check(owner):
                ready = asyncio.Event()
                roots = [f"file:///{owner}/before"]

                async def progress(*args):
                    ready.set()

                async with Client(
                    url,
                    mode="legacy",
                    roots=lambda context: roots,
                    progress_handler=progress,
                ) as client:
                    task = asyncio.create_task(client.call_tool("watch"))
                    try:
                        await ready.wait()
                        owners_ready.add(owner)
                        if len(owners_ready) == 2:
                            both_ready.set()
                        await both_ready.wait()
                        if ending == "revoke":
                            relay.authorized = lambda request: False
                            await client.session.send_roots_list_changed()
                            await asyncio.sleep(0.05)
                            if task.done() and not task.cancelled():
                                assert task.exception() is not None
                        if ending != "finish":
                            return
                        roots[:] = [f"file:///{owner}/after"]
                        await client.session.send_roots_list_changed()
                        result = await task
                        assert result.data == {
                            "before": [f"file:///{owner}/before"],
                            "after": [f"file:///{owner}/after"],
                        }
                    finally:
                        if not task.done():
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)

            await asyncio.gather(check("alice"), check("bob"))
        async with asyncio.timeout(3):
            while relay.entries:
                await asyncio.sleep(0.01)
    assert len(received) == (2 if ending == "finish" else 0)
