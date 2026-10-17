"""公开 SDK 驱动的旧 STDIO 夹具，在协商前拒绝现代发现。"""

import asyncio
import json
import os

from anyio.abc import ObjectReceiveStream
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage
import mcp.types as types

changed = asyncio.Event()


class LegacyReceive(ObjectReceiveStream):
    """只过滤探测；旧握手、关联、进度和取消均交给 SDK。"""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    async def receive(self):
        while True:
            item = await self.reader.receive()
            message = item.message if isinstance(item, SessionMessage) else None
            if (
                isinstance(message, types.JSONRPCRequest)
                and message.method == "server/discover"
            ):
                await self.writer.send(
                    SessionMessage(
                        types.JSONRPCError(
                            jsonrpc="2.0",
                            id=message.id,
                            error=types.ErrorData(
                                code=types.METHOD_NOT_FOUND, message="Legacy fixture"
                            ),
                        )
                    )
                )
            else:
                return item

    async def aclose(self):
        await self.reader.aclose()


async def list_tools(ctx, params):
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="watch",
                input_schema={"type": "object", "properties": {}},
            )
        ]
    )


async def watch(ctx, params):
    assert ctx.protocol_version == "2025-11-25"
    assert ctx.session.client_capabilities.roots.list_changed
    before = await ctx.session.list_roots()
    await ctx.session.report_progress(0, total=1, message=str(os.getpid()))
    await changed.wait()
    after = await ctx.session.list_roots()
    result = {
        "pid": os.getpid(),
        "before": [str(root.uri) for root in before.roots],
        "after": [str(root.uri) for root in after.roots],
    }
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(result))],
        structured_content=result,
    )


async def notify(ctx, params):
    changed.set()


async def main():
    server = Server("legacy stdio roots", on_list_tools=list_tools, on_call_tool=watch)
    server.add_notification_handler(
        "notifications/roots/list_changed", types.NotificationParams, notify
    )
    async with stdio_server() as (reader, writer):
        await server.run(
            LegacyReceive(reader, writer),
            writer,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
