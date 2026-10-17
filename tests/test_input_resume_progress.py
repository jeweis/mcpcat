"""用户输入前后均转发原调用进度，不重放工具。"""

import asyncio
import json

import mcp.types as types
import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from test_gateway_client_capabilities import live_http
from test_proxy_cross_generation import single_era

from app.services.mcp_factory import MCPServerFactory
from app.services.proxy_capability_transport import CapabilityTransport


def observe_notifications(app, records, delay=0):
    """仅记录合成夹具的实际 HTTP 通知，区分服务端和客户端丢失。"""

    async def application(scope, receive, send):
        pending = b""

        async def observed_send(event):
            nonlocal pending
            if event["type"] == "http.response.body":
                pending += event.get("body", b"")
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    if line.startswith(b"data: "):
                        message = json.loads(line[6:])
                        if message.get("method") == "notifications/message":
                            records.append(message["params"]["data"])
                            if delay:
                                await asyncio.sleep(delay)
            await send(event)

        await app(scope, receive, observed_send)

    return application


@pytest.mark.asyncio
@pytest.mark.parametrize("downstream", ["auto", "legacy"])
@pytest.mark.parametrize("kind", ["tool", "resource", "prompt"])
@pytest.mark.parametrize("wire_delay", [0, 0.02])
async def test_notifications_survive_component_input_resume(
    downstream, kind, wire_delay
):
    source, calls = FastMCP("progress fixture"), []
    source_wire, gateway_wire = [], []

    async def process(owner: str, ctx: Context):
        calls.append(owner)
        await ctx.report_progress(0, 2, f"{owner}:before")
        await ctx.session.send_log_message(
            "warning", {"owner": owner, "step": 0}, related_request_id=ctx.request_id
        )
        for index in (1, 2):
            reply = await ctx.session.create_message(
                messages=[
                    types.SamplingMessage(
                        role="user", content=types.TextContent(type="text", text=owner)
                    )
                ],
                max_tokens=10,
            )
            assert reply.content.text == "approved"
            await ctx.report_progress(index, 2, f"{owner}:after-{index}")
            await ctx.session.send_log_message(
                "warning",
                {"owner": owner, "step": index},
                related_request_id=ctx.request_id,
            )
        return "finished"

    source.tool()(process)

    @source.resource("fixture://notifications/{owner}")
    async def resource(owner: str, ctx: Context):
        return await process(owner, ctx)

    @source.prompt()
    async def prompt(owner: str, ctx: Context):
        return await process(owner, ctx)

    async def verify(url, owner):
        events = []
        logs = []
        wire_logs = []

        async def answer(messages, params, context):
            assert messages[0].content.text == owner
            return "approved"

        async def progress(value, total, message):
            events.append((value, total, message))

        async def log(message):
            logs.append(message.data)

        async def wire_log(message):
            wire_logs.append(message.data)

        async with Client(
            CapabilityTransport(
                StreamableHttpTransport(url, headers={"Mcpcat-Key": owner}),
                None,
                ordered_log_handler=wire_log,
            ),
            mode=downstream,
            sampling_handler=answer,
            progress_handler=progress,
            log_handler=log,
        ) as client:
            if kind == "tool":
                result = await client.call_tool("process", {"owner": owner})
                assert result.content[0].text == "finished"
            elif kind == "resource":
                result = await client.read_resource(f"fixture://notifications/{owner}")
                assert result[0].text == "finished"
            else:
                result = await client.get_prompt("prompt", {"owner": owner})
                assert result.messages[0].content.text == "finished"
        if kind == "tool":
            assert events == [
                (0, 2, f"{owner}:before"),
                (1, 2, f"{owner}:after-1"),
                (2, 2, f"{owner}:after-2"),
            ]
        evidence = {
            "source_wire": source_wire,
            "gateway_wire": gateway_wire,
            "client_wire": wire_logs,
            "callbacks": logs,
        }
        assert logs == [
            {"owner": owner, "step": index} for index in (0, 1, 2)
        ], json.dumps(evidence)

    async with asyncio.timeout(25), live_http(
        observe_notifications(
            single_era(source.http_app(path="/mcp", json_response=False), "legacy", []),
            source_wire,
            delay=wire_delay,
        )
    ) as target:
        proxy = MCPServerFactory.create_server(
            "progress", {"type": "streamable-http", "url": target, "timeout": 10}
        )
        async with live_http(
            observe_notifications(
                proxy.http_app(path="/mcp", json_response=False), gateway_wire
            )
        ) as url:
            await asyncio.gather(verify(url, "alice"), verify(url, "bob"))
    assert sorted(calls) == ["alice", "bob"]
