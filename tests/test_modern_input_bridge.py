"""真实 HTTP 恢复身份/参数隔离；旧操作只执行一次且随网关关闭。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import mcp.types as types
import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from mcp.shared.exceptions import MCPError
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
from test_gateway_client_capabilities import live_http
from test_proxy_cross_generation import single_era

from app.services.mcp_factory import MCPServerFactory
from app.services.modern_input_bridge import PREFIX, ModernInputBridge


@pytest.mark.asyncio
async def test_direct_component_does_not_revive_stopped_bridge():
    """Direct calls remain compatible; closed lifespans cannot create operations."""
    bridge = ModernInputBridge()
    context = SimpleNamespace(
        fastmcp_context=SimpleNamespace(
            request_context=SimpleNamespace(
                protocol_version=next(iter(MODERN_PROTOCOL_VERSIONS))
            ),
            request_state=None,
        )
    )
    call_next = AsyncMock(return_value="original")
    assert await bridge.run(context, call_next, None) == "original"
    assert not bridge.pending
    context.fastmcp_context.request_state = PREFIX + "unknown"
    with pytest.raises(MCPError, match="access denied"):
        await bridge.run(context, call_next, None)
    context.fastmcp_context.request_state = None
    async with bridge.lifespan(None):
        assert bridge.alive and bridge.started
    with pytest.raises(MCPError, match="access denied"):
        await bridge.run(context, call_next, None)
    call_next.assert_awaited_once_with(context)
    assert not bridge.pending


def legacy_source(calls, closed):
    """Record the side-effect boundary before the legacy reverse request."""
    source = FastMCP("old input fixture")

    @source.tool()
    async def sample(value: str, ctx: Context):
        calls.append(value)
        try:
            result = await ctx.session.create_message(
                messages=[
                    types.SamplingMessage(
                        role="user", content=types.TextContent(type="text", text=value)
                    )
                ],
                max_tokens=10,
            )
            return result.content.text
        finally:
            closed.set()

    return source


def client_at(url, owner):
    async def answer(messages, params, context):
        pytest.fail("Raw protocol test must not invoke the automatic callback")

    return Client(
        StreamableHttpTransport(url, headers={"Mcpcat-Key": owner}),
        sampling_handler=answer,
    )


async def verify_isolation(url, calls):
    """Reject other identities, changed arguments, wrong questions and replay."""
    async with client_at(url, "alice") as alice, client_at(url, "bob") as bob:
        ask = await alice.session.call_tool(
            "sample", {"value": "one"}, allow_input_required=True
        )
        assert isinstance(ask, types.InputRequiredResult)
        key = next(iter(ask.input_requests))
        reply = types.CreateMessageResult(
            role="assistant",
            model="fixture",
            content=types.TextContent(type="text", text="approved"),
        )
        for client, value, question_id in (
            (bob, "one", key),
            (alice, "changed", key),
            (alice, "one", "wrong"),
        ):
            with pytest.raises(MCPError):
                await client.session.call_tool(
                    "sample",
                    {"value": value},
                    request_state=ask.request_state,
                    input_responses={question_id: reply},
                    allow_input_required=True,
                )
            assert calls == ["one"]
        result = await alice.session.call_tool(
            "sample",
            {"value": "one"},
            request_state=ask.request_state,
            input_responses={key: reply},
            allow_input_required=True,
        )
        assert result.content[0].text == "approved"
        with pytest.raises(MCPError):
            await alice.session.call_tool(
                "sample",
                {"value": "one"},
                request_state=ask.request_state,
                input_responses={key: reply},
                allow_input_required=True,
            )
        assert calls == ["one"]


@pytest.mark.asyncio
@pytest.mark.parametrize("abandon", [False, True])
async def test_modern_bridge_isolation_and_shutdown(abandon):
    calls, closed = [], asyncio.Event()
    source = legacy_source(calls, closed)
    http = source.http_app(path="/mcp", json_response=False)
    async with asyncio.timeout(30), live_http(single_era(http, "legacy", [])) as target:
        proxy = MCPServerFactory.create_server(
            "fixture", {"type": "streamable-http", "url": target, "timeout": 15}
        )
        bridge = next(
            mw for mw in proxy.middleware if isinstance(mw, ModernInputBridge)
        )
        async with live_http(proxy.http_app(path="/mcp", json_response=False)) as url:
            if abandon:
                async with client_at(url, "alice") as client:
                    ask = await client.session.call_tool(
                        "sample",
                        {"value": "one"},
                        allow_input_required=True,
                    )
                    assert isinstance(ask, types.InputRequiredResult)
                    assert len(bridge.pending) == 1
                    assert not closed.is_set()
            else:
                await verify_isolation(url, calls)
        await asyncio.wait_for(closed.wait(), 3)
        assert not bridge.pending and not bridge.alive
        assert calls == ["one"]
