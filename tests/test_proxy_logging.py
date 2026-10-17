"""日志须经真实代理回到原请求，不能以回调已注册代替协议证据。"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.client.logging import LogMessage
from fastmcp.client.transports import StreamableHttpTransport
from mcp_types import LoggingMessageNotification, LoggingMessageNotificationParams
from mcp_types import JSONRPCNotification, JSONRPCRequest
from mcp.shared.message import SessionMessage
from pydantic import model_serializer
from test_gateway_client_capabilities import live_http
from test_proxy_cross_generation import single_era

from app.services.inspector_events import InspectorEvents
from app.services.mcp_factory import MCPServerFactory
from app.services.proxy_logging import OrderedLogStream


@pytest.mark.asyncio
async def test_ordered_log_stream_finishes_delivery_before_next_message():
    started, release = asyncio.Event(), asyncio.Event()
    messages = [
        SessionMessage(
            JSONRPCNotification(
                jsonrpc="2.0",
                method="notifications/message",
                params={"level": "warning", "data": None},
            )
        ),
        SessionMessage(JSONRPCRequest(jsonrpc="2.0", id=1, method="roots/list")),
        SessionMessage(
            JSONRPCNotification(
                jsonrpc="2.0",
                method="notifications/message",
                params={"level": "warning"},
            )
        ),
    ]
    received = []

    class Reader:
        index, closed = 0, False

        async def receive(self):
            item = messages[self.index]
            self.index += 1
            return item

        async def aclose(self):
            self.closed = True

    async def handler(message):
        started.set()
        await release.wait()
        received.append(message.data)

    reader = Reader()
    stream = OrderedLogStream(reader, handler)
    delivery = asyncio.create_task(stream.receive())
    try:
        async with asyncio.timeout(3):
            await started.wait()
            assert not delivery.done() and reader.index == 1
            release.set()
            assert await delivery is messages[0]
            assert await stream.receive() is messages[1]
            # 缺失 data 的非法通知原样交回 SDK，不猜测 null、不绕过校验。
            assert await stream.receive() is messages[2]
            assert received == [None]
    finally:
        release.set()
        await asyncio.gather(delivery, return_exceptions=True)
        await stream.aclose()
    assert reader.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("upstream", ["modern", "legacy", "sse"])
@pytest.mark.parametrize("threshold", ["warning", "emergency"])
async def test_arbitrary_json_log_data_is_not_rewritten(
    mode, upstream, threshold, caplog
):
    source = FastMCP("raw logging fixture")
    payloads = [
        {"count": 3, "nested": [True, None]},
        {"msg": "not-framework-data", "extra": {}, "preserve": True},
        [1, "text"],
        "plain",
        42,
        None,
    ]

    class ExplicitNullNotification(LoggingMessageNotification):
        @model_serializer(mode="wrap")
        def preserve_required_data(self, handler):
            result = handler(self)
            result["params"]["data"] = None
            return result

    @source.tool()
    async def emit(ctx: Context) -> str:
        for payload in payloads:
            if payload is None:
                # 独立构造合规上游 wire 消息，不复用网关的 null 适配。
                await ctx.session.send_notification(
                    ExplicitNullNotification(
                        params=LoggingMessageNotificationParams(
                            level="warning", data=None, logger="raw-fixture"
                        )
                    ),
                    related_request_id=ctx.request_id,
                )
                continue
            await ctx.session.send_log_message(
                level="warning",
                data=payload,
                logger="raw-fixture",
                related_request_id=ctx.request_id,
            )
        return ctx.request_context.protocol_version

    received = []

    async def receive(message: LogMessage) -> None:
        received.append(message.data)

    application = (
        source.http_app(path="/mcp", transport="sse")
        if upstream == "sse"
        else single_era(source.http_app(path="/mcp"), upstream, [])
    )
    async with asyncio.timeout(20), live_http(application) as target:
        proxy = MCPServerFactory.create_server(
            "raw",
            {
                "type": "sse" if upstream == "sse" else "streamable-http",
                "url": target,
                "timeout": 5,
            },
        )
        async with live_http(proxy.http_app(path="/mcp")) as url:
            async with Client(url, mode=mode, log_handler=receive) as client:
                if mode == "legacy":
                    await client.set_logging_level(threshold)
                result = await client.call_tool(
                    "emit", meta={"io.modelcontextprotocol/logLevel": threshold}
                )
                assert result.data == (
                    "2026-07-28" if upstream == "modern" else "2025-11-25"
                )
    assert received == (payloads if threshold == "warning" else [])
    assert "not-framework-data" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream", ["modern", "legacy"])
@pytest.mark.parametrize("downstream", ["auto", "legacy"])
@pytest.mark.parametrize(
    "level",
    ["debug", "info", "notice", "warning", "error", "critical", "alert", "emergency"],
)
async def test_proxy_logging_levels_and_concurrent_request_isolation(
    upstream, downstream, level
):
    source = FastMCP("logging fixture")

    @source.tool()
    async def emit(owner: str, ctx: Context) -> dict:
        for severity in (
            "debug",
            "info",
            "notice",
            "warning",
            "error",
            "critical",
            "alert",
            "emergency",
        ):
            await ctx.log(owner, level=severity, logger_name="fixture")
            await asyncio.sleep(0.001)
        return {"owner": owner, "protocol": ctx.request_context.protocol_version}

    async with asyncio.timeout(20), live_http(
        single_era(source.http_app(path="/mcp"), upstream, [])
    ) as target:
        proxy = MCPServerFactory.create_server(
            "logging",
            {"type": "streamable-http", "url": target, "timeout": 5},
        )
        async with live_http(proxy.http_app(path="/mcp")) as url:

            async def verify(owner: str) -> None:
                messages = []

                async def receive(message: LogMessage) -> None:
                    messages.append(message)

                async with Client(url, mode=downstream, log_handler=receive) as client:
                    assert client.protocol_version == (
                        "2026-07-28" if downstream == "auto" else "2025-11-25"
                    )
                    if downstream == "legacy":
                        await client.set_logging_level(level)
                    result = await client.call_tool(
                        "emit",
                        {"owner": owner},
                        meta={"io.modelcontextprotocol/logLevel": level},
                    )
                    assert result.data == {
                        "owner": owner,
                        "protocol": (
                            "2026-07-28" if upstream == "modern" else "2025-11-25"
                        ),
                    }
                levels = [
                    "debug",
                    "info",
                    "notice",
                    "warning",
                    "error",
                    "critical",
                    "alert",
                    "emergency",
                ]
                expected = levels[levels.index(level) :]
                assert [message.level for message in messages] == expected
                assert all(message.logger == "fixture" for message in messages)
                assert all(message.data["msg"] == owner for message in messages)

            await asyncio.gather(verify("alice-fixture"), verify("bob-fixture"))


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream", ["modern", "legacy"])
async def test_modern_request_without_log_opt_in_receives_no_notifications(upstream):
    class QuietTransport(StreamableHttpTransport):
        @asynccontextmanager
        async def connect_session(self, **kwargs):
            # SDK 公开会话构造选项；不改写 Client 私有属性。
            kwargs["log_level"] = None
            async with super().connect_session(**kwargs) as session:
                yield session

    source = FastMCP("quiet logging fixture")

    @source.tool()
    async def emit(ctx: Context) -> str:
        await ctx.warning("fixture message")
        return "done"

    messages = []

    async def receive(message: LogMessage) -> None:
        messages.append(message)

    async with asyncio.timeout(20), live_http(
        single_era(source.http_app(path="/mcp"), upstream, [])
    ) as target:
        proxy = MCPServerFactory.create_server(
            "quiet", {"type": "streamable-http", "url": target, "timeout": 5}
        )
        async with live_http(proxy.http_app(path="/mcp")) as url:
            async with Client(QuietTransport(url), log_handler=receive) as client:
                assert client.protocol_version == "2026-07-28"
                assert (await client.call_tool("emit")).data == "done"
    assert messages == []


@pytest.mark.asyncio
async def test_inspector_log_events_are_bounded_and_do_not_retain_payload():
    events = InspectorEvents()
    for _ in range(205):
        await events.log(
            LogMessage(level="warning", logger="fixture", data={"private": "omitted"})
        )
    snapshot = events.snapshot()
    assert snapshot["dropped"] == 5
    assert len(snapshot["events"]) == 200
    assert all(row["content_omitted"] for row in snapshot["events"])
    assert "private" not in str(snapshot) and "fixture" not in str(snapshot)
    await events.close()
    await events.log(LogMessage(level="info", logger="fixture", data="late"))
    assert events.snapshot()["events"] == []
