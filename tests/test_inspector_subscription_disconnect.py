"""通过真实 TCP 断流验证 Inspector，不伪造协议异常或 UI 状态。"""

import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import SSETransport
from mcp.server.mcpserver import MCPServer
from test_gateway_client_capabilities import live_http
from test_inspector_events import wait_for
from test_proxy_subscriptions import CountingBus
from test_proxy_legacy_subscriptions import LegacySource
from test_proxy_cross_generation import single_era

from app.services.inspector_events import InspectorEvents


@asynccontextmanager
async def cuttable_proxy(upstream: str):
    """只转发本机 TCP 字节，测试事件关闭全部在途连接。"""
    target = urlsplit(upstream)
    dropped = asyncio.Event()
    workers = set()

    async def copy(reader, writer):
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()

    async def connect(reader, writer):
        task = asyncio.current_task()
        workers.add(task)
        outgoing = None
        pumps = []
        try:
            if dropped.is_set():
                return
            incoming, outgoing = await asyncio.open_connection(target.hostname, target.port)
            pumps = [
                asyncio.create_task(copy(reader, outgoing)),
                asyncio.create_task(copy(incoming, writer)),
                asyncio.create_task(dropped.wait()),
            ]
            await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            writer.close()
            if outgoing is not None:
                outgoing.close()
            workers.discard(task)

    server = await asyncio.start_server(connect, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}{target.path}", dropped
    finally:
        dropped.set()
        server.close()
        await server.wait_closed()
        await asyncio.gather(*list(workers), return_exceptions=True)


@pytest.mark.asyncio
async def test_modern_network_drop_exits_worker_and_releases_source() -> None:
    """断流必须释放源订阅，不保留虚假的 listening 状态。"""
    bus = CountingBus()
    source = MCPServer("disconnect fixture", subscriptions=bus)
    events = InspectorEvents()
    async with asyncio.timeout(20), live_http(source.streamable_http_app()) as upstream:
        async with cuttable_proxy(upstream) as (endpoint, dropped):
            try:
                events.start(Client(endpoint), {"tools_list_changed": True}, lambda: True)
                await wait_for(lambda: events.subscription["status"] == "listening")
                assert bus.active == 1
                dropped.set()
                await wait_for(lambda: events.worker.done() and bus.active == 0)
                assert events.subscription["status"] in {"ended", "failed"}
            finally:
                await events.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
async def test_legacy_network_drop_exits_inspector_worker(transport: str) -> None:
    """旧流中断也不能让测试台永久 sleep 并显示 listening。"""
    fixture = LegacySource()
    source = FastMCP("legacy disconnect", middleware=[fixture])
    app = source.http_app(path="/mcp", json_response=False, transport=transport)
    application = single_era(app, "legacy", []) if transport == "streamable-http" else app
    events = InspectorEvents()
    async with asyncio.timeout(20), live_http(application) as upstream:
        async with cuttable_proxy(upstream) as (endpoint, dropped):
            target = SSETransport(endpoint) if transport == "sse" else endpoint
            try:
                client = Client(target, mode="legacy", message_handler=events)
                events.start(client, {"resource_subscriptions": ["data://one"]}, lambda: True)
                await wait_for(lambda: events.subscription["status"] == "listening")
                dropped.set()
                async with asyncio.timeout(15):
                    while not events.worker.done():
                        await asyncio.sleep(0.01)
                assert events.subscription["status"] in {"ended", "failed"}
            finally:
                await events.close()
