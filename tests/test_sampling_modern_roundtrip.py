"""现代 Sampling 的多轮输入走公开代理与客户端驱动，不调用旧反向通道。"""

import asyncio

import mcp.types as types
import pytest
from fastmcp import Client, Context, FastMCP
from test_gateway_client_capabilities import live_http
from test_proxy_cross_generation import single_era

from app.services.mcp_factory import MCPServerFactory


def sampling_source(calls):
    """A modern source that completes only after the explicit input round."""
    source = FastMCP("modern sampling fixture")

    @source.tool()
    async def sample(ctx: Context):
        if ctx.input_responses is None:
            return types.InputRequiredResult(
                input_requests={
                    "sample": types.CreateMessageRequest(
                        method="sampling/createMessage",
                        params=types.CreateMessageRequestParams(
                            messages=[
                                types.SamplingMessage(
                                    role="user",
                                    content=types.TextContent(
                                        type="text", text="fixture"
                                    ),
                                )
                            ],
                            max_tokens=10,
                        ),
                    )
                }
            )
        calls.append("completed")
        return ctx.input_responses["sample"].model_dump(by_alias=True)

    return source


@pytest.mark.asyncio
@pytest.mark.parametrize("downstream", ["auto", "legacy"])
async def test_modern_sampling_roundtrip_preserves_client_result(downstream):
    calls, answers = [], []
    source = sampling_source(calls)

    async def answer(messages, params, context):
        answers.append(params)
        return types.CreateMessageResult(
            role="assistant",
            model="client-fixture",
            stop_reason="endTurn",
            content=types.TextContent(type="text", text="approved"),
        )

    http = source.http_app(path="/mcp", json_response=False)
    async with asyncio.timeout(20), live_http(single_era(http, "modern", [])) as target:
        proxy = MCPServerFactory.create_server(
            "sampling", {"type": "streamable-http", "url": target, "timeout": 5}
        )
        async with Client(proxy, mode=downstream, sampling_handler=answer) as client:
            assert client.protocol_version == (
                "2026-07-28" if downstream == "auto" else "2025-11-25"
            )
            result = await client.call_tool("sample")
            assert result.data["model"] == "client-fixture"
            assert result.data["content"]["text"] == "approved"
            assert result.data["stopReason"] == "endTurn"
    assert len(answers) == 1 and calls == ["completed"]
