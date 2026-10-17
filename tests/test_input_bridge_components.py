"""资源模板与提示模板的输入轮次必须保留参数、状态及最终内容。"""

import asyncio

import mcp.types as types
import pytest
from fastmcp import Client, Context, FastMCP
from test_gateway_client_capabilities import live_http
from test_proxy_cross_generation import single_era

from app.services.mcp_factory import MCPServerFactory


def input_source(completed):
    """Modern-only source with an opaque state-bearing input round."""
    source = FastMCP("component input fixture")

    def content(name: str, ctx: Context):
        if ctx.input_responses is None:
            return types.InputRequiredResult(
                request_state="fixture-state:" + name,
                input_requests={
                    "sample": types.CreateMessageRequest(
                        method="sampling/createMessage",
                        params=types.CreateMessageRequestParams(
                            messages=[
                                types.SamplingMessage(
                                    role="user",
                                    content=types.TextContent(type="text", text=name),
                                )
                            ],
                            max_tokens=10,
                        ),
                    )
                },
            )
        assert ctx.request_state == "fixture-state:" + name
        completed.append(name)
        return ctx.input_responses["sample"].content.text

    @source.resource("fixture://input/{name}")
    async def resource(name: str, ctx: Context):
        return content(name, ctx)

    @source.prompt()
    async def prompt(name: str, ctx: Context):
        return content(name, ctx)

    return source


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["resource", "prompt"])
@pytest.mark.parametrize("downstream", ["auto", "legacy"])
async def test_component_input_roundtrip(kind, downstream):
    completed, questions = [], []
    source = input_source(completed)

    async def answer(messages, params, context):
        questions.append(messages[0].content.text)
        return "client answer"

    http = source.http_app(path="/mcp", json_response=False)
    async with asyncio.timeout(20), live_http(single_era(http, "modern", [])) as target:
        proxy = MCPServerFactory.create_server(
            "fixture", {"type": "streamable-http", "url": target, "timeout": 5}
        )
        async with Client(proxy, mode=downstream, sampling_handler=answer) as client:
            if kind == "resource":
                result = await client.read_resource("fixture://input/one")
                assert result[0].text == "client answer"
            else:
                result = await client.get_prompt("prompt", {"name": "one"})
                assert result.messages[0].content.text == "client answer"
    assert completed == ["one"] and questions == ["one"]
