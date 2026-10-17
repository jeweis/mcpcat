"""可信 STDIO 日志来源：只发送合成消息，不读取磁盘或用户凭证。"""

import asyncio
import json
import os
import sys

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from pydantic import model_serializer

from mcp_stdio_roots import LegacyReceive


class ExplicitNullNotification(types.LoggingMessageNotification):
    @model_serializer(mode="wrap")
    def preserve_required_data(self, handler):
        result = handler(self)
        result["params"]["data"] = None
        return result


async def list_tools(ctx, params):
    return types.ListToolsResult(
        tools=[types.Tool(name="emit", input_schema={"type": "object"})]
    )


async def emit(ctx, params):
    owner = params.arguments["owner"]
    for level in (
        "debug",
        "info",
        "notice",
        "warning",
        "error",
        "critical",
        "alert",
        "emergency",
    ):
        await ctx.session.send_log_message(
            level,
            {"owner": owner, "nested": [True, None]},
            logger="stdio-fixture",
            related_request_id=ctx.request_id,
        )
    await ctx.session.send_notification(
        ExplicitNullNotification(
            params=types.LoggingMessageNotificationParams(
                level="warning", data=None, logger="stdio-fixture"
            )
        ),
        related_request_id=ctx.request_id,
    )
    result = {"pid": os.getpid(), "protocol": ctx.protocol_version}
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(result))],
        structured_content=result,
    )


async def set_level(ctx, params):
    return types.EmptyResult()


async def main():
    server = Server(
        "stdio logs",
        on_list_tools=list_tools,
        on_call_tool=emit,
        on_set_logging_level=set_level,
    )
    async with stdio_server() as (reader, writer):
        if sys.argv[-1] == "legacy":
            reader = LegacyReceive(reader, writer)
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
