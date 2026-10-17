"""真实 SDK 订阅、进度与会话缓冲区边界。"""

import asyncio
from types import SimpleNamespace

import pytest
from fastmcp import Client, Context, FastMCP
from mcp.server.mcpserver import MCPServer
from mcp.server.subscriptions import InMemorySubscriptionBus
from mcp.shared.subscriptions import ResourceUpdated, ToolsListChanged

from app.services.inspector_events import InspectorEvents


async def wait_for(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


def test_ring_reports_lost_events_and_close_boundary():
    events = InspectorEvents()
    for i in range(205):
        events.append("fixture", value=i)
    snapshot = events.snapshot()
    assert len(snapshot["events"]) == 200
    assert snapshot["dropped"] == 5
    assert snapshot["cursor"] == 205
    assert len(events.snapshot(204)["events"]) == 1
    assert events.snapshot(205)["dropped"] == 0


@pytest.mark.asyncio
async def test_legacy_changes_require_active_honored_filter():
    events = InspectorEvents()
    tools = SimpleNamespace(method="notifications/tools/list_changed")
    prompts = SimpleNamespace(method="notifications/prompts/list_changed")
    resource = lambda uri: SimpleNamespace(
        method="notifications/resources/updated", params=SimpleNamespace(uri=uri)
    )
    await events.on_notification(tools)
    assert not events.items
    events.subscription = {
        "mode": "legacy", "status": "listening",
        "honored": {"toolsListChanged": True, "resourceSubscriptions": ["data://one"]},
    }
    await events.on_notification(tools)
    await events.on_notification(prompts)
    await events.on_notification(resource("data://other"))
    await events.on_notification(resource("data://one"))
    assert [row["kind"] for row in events.items] == [
        "notifications/tools/list_changed", "notifications/resources/updated",
    ]
    for status in ("connecting", "stopping", "stopped", "failed", "revoked"):
        events.subscription["status"] = status
        await events.on_notification(tools)
        await events.on_notification(resource("data://one"))
        assert len(events.items) == 2
    await events.close()


@pytest.mark.asyncio
async def test_revoked_identity_cannot_refill_diagnostic_buffer():
    events = InspectorEvents()
    events.subscription["status"] = "revoked"
    await events.progress(1, 2, None)
    await events.log(SimpleNamespace(level="info"))
    assert not events.items


@pytest.mark.asyncio
async def test_real_modern_listen_filters_stops_and_revokes():
    bus = InMemorySubscriptionBus()
    server = MCPServer("subscriptions fixture", subscriptions=bus)
    events = InspectorEvents()
    client = Client(server, message_handler=events)
    permitted = True
    filters = dict(tools_list_changed=True, resource_subscriptions=["data://one"])
    try:
        events.start(client, filters, lambda: permitted)
        await wait_for(lambda: events.subscription["status"] != "connecting")
        assert events.subscription["status"] == "listening", events.subscription
        assert events.subscription["honored"]["toolsListChanged"]
        with pytest.raises(ValueError):
            events.start(client, filters, lambda: True)
        await bus.publish(ToolsListChanged())
        await bus.publish(ResourceUpdated("data://other"))
        await bus.publish(ResourceUpdated("data://one"))
        await wait_for(lambda: len(events.items) == 2)
        assert "data://other" not in str(events.snapshot())
        await events.stop()
        await bus.publish(ToolsListChanged())
        await asyncio.sleep(0.01)
        assert len(events.items) == 2
        events.start(client, filters, lambda: permitted)
        await wait_for(lambda: events.subscription["status"] == "listening")
        permitted = False
        await wait_for(lambda: events.worker.done())
        assert events.subscription["status"] == "revoked"
        assert not events.items
    finally:
        await events.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_real_progress_keeps_numeric_values_without_secret_text(mode):
    server = FastMCP("progress fixture")

    @server.tool()
    async def work(ctx: Context) -> str:
        await ctx.report_progress(1, 2, "secret progress detail")
        return "done"

    events = InspectorEvents()
    async with Client(server, mode=mode, message_handler=events,
                      progress_handler=events.progress) as client:
        await client.call_tool("work")
        await wait_for(lambda: bool(events.items))
    row = events.snapshot()["events"][0]
    assert row["kind"] == "progress"
    assert row["progress"] == 1 and row["total"] == 2
    assert "secret" not in str(events.snapshot())
    await events.close()
    events.append("late")
    assert not events.items
