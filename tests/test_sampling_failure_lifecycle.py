"""Sampling 拒绝与取消通过真实网关往返，不调用真实模型。"""

import asyncio
from contextlib import asynccontextmanager

import mcp.types as types
import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from test_gateway_client_capabilities import live_http
from test_proxy_cross_generation import single_era

from app.services.mcp_factory import MCPServerFactory


@asynccontextmanager
async def sampling_gateway(finished):
    """Serve a legacy sampling source through the production proxy factory."""
    source = FastMCP("sampling failure fixture")

    @source.tool()
    async def sample(ctx: Context) -> str:
        try:
            result = await ctx.session.create_message(
                messages=[
                    types.SamplingMessage(
                        role="user",
                        content=types.TextContent(type="text", text="fixture"),
                    )
                ],
                max_tokens=10,
            )
            return result.content.text
        finally:
            finished.set()

    http = source.http_app(path="/mcp", json_response=False)
    async with live_http(single_era(http, "legacy", [])) as upstream:
        proxy = MCPServerFactory.create_server(
            "sampling", {"type": "streamable-http", "url": upstream, "timeout": 15}
        )
        async with live_http(proxy.http_app(path="/mcp", json_response=False)) as url:
            yield url


@pytest.mark.asyncio
async def test_sampling_refusal_returns_error_without_retry():
    finished = asyncio.Event()
    calls = []

    async def refuse(messages, params, context):
        calls.append(params)
        raise MCPError(types.INVALID_REQUEST, "Fixture user declined sampling")

    async with asyncio.timeout(30), sampling_gateway(finished) as url:
        async with Client(url, mode="legacy", sampling_handler=refuse) as client:
            with pytest.raises(ToolError):
                await client.call_tool("sample")
            await asyncio.wait_for(finished.wait(), 3)
            assert len(calls) == 1


@pytest.mark.asyncio
async def test_sampling_cancellation_releases_both_ends_and_keeps_next_client_usable():
    entered, closed, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def wait_for_user(messages, params, context):
        calls.append("waiting")
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    async with asyncio.timeout(30), sampling_gateway(finished) as url:
        async with Client(url, mode="legacy", sampling_handler=wait_for_user) as client:
            pending = asyncio.create_task(client.call_tool("sample"))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                await asyncio.wait_for(finished.wait(), 5)
                await asyncio.wait_for(closed.wait(), 5)
                assert calls == ["waiting"]
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

        async def answer(messages, params, context):
            return "new client only"

        async with Client(url, mode="legacy", sampling_handler=answer) as other:
            assert (await other.call_tool("sample")).data == "new client only"
        assert calls == ["waiting"]
