"""测试台撤权必须释放真实旧协议订阅，不能仅更新页面状态。"""

import asyncio

import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import SSETransport
from test_gateway_client_capabilities import live_http
from test_inspector_events import wait_for
from test_proxy_cross_generation import single_era
from test_proxy_legacy_subscriptions import LegacySource

from app.services.inspector_events import InspectorEvents


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
async def test_legacy_revocation_unsubscribes_and_requires_explicit_restart(
    transport: str,
) -> None:
    """恢复权限不复活旧连接，源服务实际收到 unsubscribe。"""
    fixture = LegacySource()
    source = FastMCP("revocation fixture", middleware=[fixture])
    app = source.http_app(path="/mcp", json_response=False, transport=transport)
    wire = []
    application = (
        single_era(app, "legacy", wire)
        if transport == "streamable-http" else app
    )
    events = InspectorEvents()
    permitted = True
    filters = {"resource_subscriptions": ["data://one"]}
    async with asyncio.timeout(20), live_http(application) as endpoint:
        target = SSETransport(endpoint) if transport == "sse" else endpoint
        client = Client(target, mode="legacy", message_handler=events)
        try:
            events.start(client, filters, lambda: permitted)
            await wait_for(lambda: events.subscription["status"] == "listening")
            await fixture.subscribed["data://one"].send_resource_updated("data://one")
            await wait_for(lambda: len(events.items) == 1)
            await asyncio.sleep(5.1)
            assert events.subscription["status"] == "listening"
            assert not events.worker.done()
            worker = events.worker
            permitted = False
            await wait_for(lambda: worker.done())
            assert events.subscription["status"] == "revoked"
            assert not events.items
            assert not fixture.subscribed
            assert fixture.removed == ["data://one"]
            permitted = True
            await asyncio.sleep(1.1)
            assert events.worker is worker and worker.done()
            assert not fixture.subscribed and not events.items
            events.start(client, filters, lambda: permitted)
            await wait_for(lambda: events.subscription["status"] == "listening")
            await events.stop()
            assert fixture.removed == ["data://one", "data://one"]
        finally:
            await events.close()


@pytest.mark.asyncio
async def test_revoked_before_connect_clears_previous_diagnostics() -> None:
    """开始前已经撤权时，同样清空旧诊断。"""
    events = InspectorEvents()
    events.append("progress", progress=1)
    client = Client(FastMCP("must not connect"))
    try:
        events.start(client, {}, lambda: False)
        await wait_for(lambda: events.worker.done())
        assert events.subscription["status"] == "revoked"
        assert not events.items
    finally:
        await events.close()
