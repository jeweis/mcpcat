"""客户端侧能力必须到达当前客户端，不读取网关主机文件或调用真实模型。"""

import asyncio
import socket
from contextlib import asynccontextmanager

import pytest
import uvicorn
import mcp.types as types
from fastmcp import Client, Context, FastMCP
from test_proxy_cross_generation import single_era

from app.services.async_compat import operation_timeout
from app.services.mcp_factory import MCPServerFactory


@asynccontextmanager
async def live_http(app):
    """反向请求必须使用流式网络，ASGITransport 会缓冲整个响应。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with operation_timeout(5):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("夹具服务未启动")
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"
        finally:
            server.should_exit = True
            try:
                async with operation_timeout(5):
                    await task
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_legacy_roots_relay_keeps_client_identity():
    source = FastMCP("roots relay fixture")

    @source.tool()
    async def client_roots(ctx: Context) -> list[str]:
        result = await ctx.session.list_roots()
        return [str(root.uri) for root in result.roots]

    http = source.http_app(path="/mcp", json_response=False)
    evidence = []
    async with operation_timeout(20), live_http(
        single_era(http, "legacy", evidence)
    ) as target:
        proxy = MCPServerFactory.create_server(
            "roots",
            {
                "type": "streamable-http",
                "url": target,
                "timeout": 5,
            },
        )

        async def verify_client(name):
            root = f"file:///fixture/{name}"
            async with Client(proxy, mode="legacy", roots=[root]) as client:
                result = await client.call_tool("client_roots")
                assert result.data == [root]

        await asyncio.gather(verify_client("alice"), verify_client("bob"))
    assert "initialize" in evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("downstream", ["auto", "legacy"])
async def test_sampling_capability_and_tools_are_forwarded(enabled, downstream):
    source = FastMCP("sampling capability fixture")
    received = []
    response = types.CreateMessageResultWithTools(
        role="assistant",
        model="fixture-model",
        stop_reason="toolUse",
        content=[
            types.ToolUseContent(
                type="tool_use", id="fixture-call", name="lookup", input={}
            )
        ],
    )

    @source.tool()
    async def sample(ctx: Context) -> dict:
        sampling = ctx.session.client_capabilities.sampling
        if sampling is None:
            return {"supported": False}
        result = await ctx.session.create_message(
            messages=[
                types.SamplingMessage(
                    role="user",
                    content=types.TextContent(type="text", text="fixture"),
                )
            ],
            max_tokens=20,
            include_context="thisServer",
            tools=[types.Tool(name="lookup", input_schema={"type": "object"})],
            tool_choice=types.ToolChoice(mode="required"),
        )
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def answer(messages, params, context):
        received.append(params)
        return response

    http = source.http_app(path="/mcp", json_response=False)
    async with operation_timeout(20), live_http(
        single_era(http, "legacy", [])
    ) as target:
        proxy = MCPServerFactory.create_server(
            "sampling",
            {
                "type": "streamable-http",
                "url": target,
                "timeout": 5,
            },
        )
        options = (
            {
                "sampling_handler": answer,
                "sampling_capabilities": types.SamplingCapability(tools={}, context={}),
            }
            if enabled
            else {}
        )
        async with Client(proxy, mode=downstream, **options) as client:
            assert client.protocol_version == (
                "2026-07-28" if downstream == "auto" else "2025-11-25"
            )
            result = await client.call_tool("sample")
    if enabled:
        assert result.data == response.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        assert len(received) == 1
        assert received[0].include_context == "thisServer"
        assert received[0].tools[0].name == "lookup"
        assert received[0].tool_choice.mode == "required"
    else:
        assert result.data == {"supported": False}
        assert not received


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        types.TextContent(type="text", text="fixture answer"),
        types.ImageContent(type="image", data="AA==", mime_type="image/png"),
        types.AudioContent(type="audio", data="AA==", mime_type="audio/wav"),
    ],
)
async def test_legacy_sampling_preserves_request_and_result(content):
    source = FastMCP("sampling relay fixture")
    response = types.CreateMessageResult(
        role="assistant",
        model="client-selected-model",
        content=content,
        stop_reason="endTurn",
    )
    requests = []

    @source.tool()
    async def sample(ctx: Context) -> dict:
        result = await ctx.session.create_message(
            messages=[
                types.SamplingMessage(
                    role="user",
                    content=types.TextContent(type="text", text="fixture prompt"),
                )
            ],
            max_tokens=23,
            system_prompt="fixture system",
            temperature=0.4,
            metadata={"fixture": "preserve"},
        )
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def sampling_handler(messages, params, context):
        requests.append(params)
        return response

    http = source.http_app(path="/mcp", json_response=False)
    async with operation_timeout(20), live_http(
        single_era(http, "legacy", [])
    ) as target:
        proxy = MCPServerFactory.create_server(
            "sampling",
            {
                "type": "streamable-http",
                "url": target,
                "timeout": 5,
            },
        )
        async with Client(
            proxy, mode="legacy", sampling_handler=sampling_handler
        ) as client:
            result = await client.call_tool("sample")
            assert result.data == response.model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
    assert len(requests) == 1
    assert requests[0].metadata == {"fixture": "preserve"}
    assert requests[0].max_tokens == 23
    assert requests[0].system_prompt == "fixture system"
    assert requests[0].temperature == 0.4


@pytest.mark.asyncio
async def test_sampling_concurrent_clients_do_not_share_callbacks():
    source = FastMCP("sampling isolation fixture")

    @source.tool()
    async def sample(ctx: Context) -> str:
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

    http = source.http_app(path="/mcp", json_response=False)
    async with operation_timeout(20), live_http(
        single_era(http, "legacy", [])
    ) as target:
        proxy = MCPServerFactory.create_server(
            "sampling",
            {
                "type": "streamable-http",
                "url": target,
                "timeout": 5,
            },
        )

        async def verify_client(name):
            async def answer(messages, params, context):
                await asyncio.sleep(0.03)
                return name

            async with Client(proxy, mode="legacy", sampling_handler=answer) as client:
                result = await client.call_tool("sample")
                assert result.data == name

        await asyncio.gather(verify_client("alice"), verify_client("bob"))
