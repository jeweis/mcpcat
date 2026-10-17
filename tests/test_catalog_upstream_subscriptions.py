"""真实聚合上游通知、映射资源 URI 和新旧客户端。"""

import asyncio

import pytest
from fastmcp import Client, FastMCP
from fastmcp.apps import UI_MIME_TYPE
from fastmcp.client.messages import MessageHandler
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.tools import Tool
from mcp.client.subscriptions import listen
from mcp.server.subscriptions import ListenHandler
from mcp.shared.exceptions import MCPError
from mcp.shared.subscriptions import (
    ResourceUpdated,
    ResourcesListChanged,
    ToolsListChanged,
)
from test_gateway_client_capabilities import live_http
from test_proxy_subscriptions import CountingBus, wait_for
from test_proxy_cross_generation import single_era
from test_proxy_legacy_subscriptions import LegacySource
from test_skill_content_workflow import READ, content_app
from test_subscription_public_adapter import PublicListenAdapter

from app.application import create_app
from app.services.catalog_app_identity import decode_catalog_resource
from app.services.config_service import ConfigService
from app.services.inspector_service import inspector_service
from app.services.market_service import MarketService
from app.services.server_manager import MCPServerManager


class Recorder(MessageHandler):
    def __init__(self):
        self.uris, self.methods = [], []

    async def on_notification(self, message):
        self.methods.append(message.method)
        if message.method == "notifications/resources/updated":
            self.uris.append(str(message.params.uri))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("source_transport", ["streamable-http", "sse"])
async def test_catalog_legacy_source_resource_updates_and_cleanup(
    content_app, monkeypatch, mode, source_transport
):
    """真实旧上游两种传输经过生产 Catalog，通知保持 URI 隔离并取消。"""
    _, database = content_app
    fixture = LegacySource()
    source = FastMCP("old catalog source", middleware=[fixture])
    source.add_tool(
        Tool.from_function(
            lambda: "unused",
            name="open",
            meta={"ui": {"resourceUri": "ui://legacy/view.html"}},
        )
    )

    @source.resource("ui://legacy/view.html", mime_type=UI_MIME_TYPE)
    def ui():
        return "<html><body>legacy fixture</body></html>"

    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    ConfigService.update_setting_section("catalog", {"enabled": True})
    source_app = source.http_app(path="/mcp", transport=source_transport)
    if source_transport == "streamable-http":
        source_app = single_era(source_app, "legacy", [])
    async with asyncio.timeout(50), live_http(source_app) as upstream:
        assert ConfigService.add_server_to_config(
            "alpha",
            {
                "type": source_transport,
                "url": upstream,
                "require_auth": True,
                "expose_in_catalog": True,
            },
        )
        app = create_app(database=database, server_manager=MCPServerManager())
        async with live_http(app) as url:
            recorder = Recorder()
            transport = StreamableHttpTransport(
                url + "/mcpcat/", headers={"Mcpcat-Key": READ}
            )
            async with Client(transport, mode=mode, message_handler=recorder) as client:
                uri = str((await client.list_resources())[0].uri)
                if mode == "auto":
                    async with listen(
                        client.session, resource_subscriptions=[uri]
                    ) as events:
                        assert events.honored.resource_subscriptions == [uri]
                        session = fixture.subscribed["ui://legacy/view.html"]
                        await session.send_resource_updated(
                            "ui://legacy/unrequested.html"
                        )
                        await session.send_resource_updated("ui://legacy/view.html")
                        event = await asyncio.wait_for(anext(events), 5)
                        assert isinstance(event, ResourceUpdated) and event.uri == uri
                else:
                    await client.session.subscribe_resource(uri)
                    session = fixture.subscribed["ui://legacy/view.html"]
                    await session.send_resource_updated("ui://legacy/unrequested.html")
                    await session.send_resource_updated("ui://legacy/view.html")
                    await wait_for(lambda: recorder.uris == [uri])
                    await client.session.unsubscribe_resource(uri)
                await wait_for(lambda: not fixture.subscribed)
            subscriptions = app.state.server_manager.catalog_service.subscriptions
            await wait_for(
                lambda: subscriptions.active == 0 and not subscriptions.legacy.entries
            )
    assert "ui://legacy/view.html" in fixture.removed


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_catalog_upstream_mapped_resources_and_changed_app_directory(
    content_app, monkeypatch, mode
):
    _, database = content_app
    bus = CountingBus()
    source = FastMCP("upstream", middleware=[PublicListenAdapter(ListenHandler(bus))])
    source.add_tool(
        Tool.from_function(
            lambda: "not executed",
            name="open",
            meta={
                "ui": {
                    "resourceUri": "ui://source/view.html",
                    "visibility": ["app", "model"],
                }
            },
        )
    )

    @source.resource("ui://source/view.html", mime_type=UI_MIME_TYPE)
    def ui():
        return "<html><body>fixture</body></html>"

    @source.resource("file://fixture/attachment.txt")
    def attachment():
        return "fixture attachment"

    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    ConfigService.update_setting_section("catalog", {"enabled": True})
    async with asyncio.timeout(50), live_http(source.http_app(path="/mcp")) as upstream:
        assert ConfigService.add_server_to_config(
            "alpha",
            {
                "type": "streamable-http",
                "url": upstream,
                "require_auth": True,
                "expose_in_catalog": True,
            },
        )
        app = create_app(database=database, server_manager=MCPServerManager())
        async with live_http(app) as url:
            transport = StreamableHttpTransport(
                url + "/mcpcat/", headers={"Mcpcat-Key": READ}
            )
            recorder = Recorder()
            async with Client(transport, mode=mode, message_handler=recorder) as client:
                resource = (await client.list_resources())[0]
                identity, original = decode_catalog_resource(str(resource.uri))
                assert original == "ui://source/view.html"
                mapped_file = identity.resource("file://fixture/attachment.txt")
                if mode == "auto":
                    async with listen(
                        client.session,
                        tools_list_changed=True,
                        resources_list_changed=True,
                        resource_subscriptions=[str(resource.uri), mapped_file],
                    ) as events:
                        assert set(events.honored.resource_subscriptions) == {
                            str(resource.uri),
                            mapped_file,
                        }
                        await bus.publish(
                            ResourceUpdated("file://fixture/attachment.txt")
                        )
                        updated = await asyncio.wait_for(anext(events), 5)
                        assert (
                            isinstance(updated, ResourceUpdated)
                            and updated.uri == mapped_file
                        )
                        source.disable(names={"open"}, components={"tool"})
                        await bus.publish(ToolsListChanged())
                        pair = [
                            await asyncio.wait_for(anext(events), 5) for _ in range(2)
                        ]
                        assert {type(event) for event in pair} == {
                            ResourcesListChanged,
                            ToolsListChanged,
                        }
                        assert await client.list_resources() == []
                else:
                    await client.session.subscribe_resource(mapped_file)
                    await bus.publish(ResourceUpdated("file://fixture/attachment.txt"))
                    await wait_for(lambda: recorder.uris == [mapped_file])
                    await client.session.unsubscribe_resource(mapped_file)
                    await bus.publish(ResourceUpdated("file://fixture/attachment.txt"))
                    await asyncio.sleep(0.1)
                    assert recorder.uris == [mapped_file]
                    source.disable(names={"open"}, components={"tool"})
                    await bus.publish(ToolsListChanged())
                    await wait_for(
                        lambda: "notifications/tools/list_changed" in recorder.methods
                        and "notifications/resources/list_changed" in recorder.methods
                    )
                    assert await client.list_resources() == []
            await wait_for(lambda: bus.active == 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_catalog_source_exclusion_closes_and_reinclusion_reconnects(
    content_app, monkeypatch, mode
):
    """运行中移出聚合应释放来源连接，重新纳入后恢复真实通知。"""
    _, database = content_app
    bus = CountingBus()
    source = FastMCP("lifecycle", middleware=[PublicListenAdapter(ListenHandler(bus))])
    source.add_tool(
        Tool.from_function(
            lambda: "unused",
            name="open",
            meta={"ui": {"resourceUri": "ui://fixture/lifecycle.html"}},
        )
    )

    @source.resource("ui://fixture/lifecycle.html", mime_type=UI_MIME_TYPE)
    def ui():
        return "<html><body>lifecycle fixture</body></html>"

    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    ConfigService.update_setting_section("catalog", {"enabled": True})
    async with asyncio.timeout(50), live_http(source.http_app(path="/mcp")) as upstream:
        config = {
            "type": "streamable-http",
            "url": upstream,
            "require_auth": True,
            "expose_in_catalog": True,
        }
        assert ConfigService.add_server_to_config("alpha", config)
        app = create_app(database=database, server_manager=MCPServerManager())
        async with live_http(app) as url:
            recorder = Recorder()
            transport = StreamableHttpTransport(
                url + "/mcpcat/", headers={"Mcpcat-Key": READ}
            )
            async with Client(transport, mode=mode, message_handler=recorder) as client:
                initial_resources = await client.list_resources()
                assert len(initial_resources) == 1
                original_uri = str(initial_resources[0].uri)

                async def change_membership(included):
                    assert ConfigService.update_server_config(
                        "alpha", {**config, "expose_in_catalog": included}
                    )
                    await wait_for(lambda: bus.active == int(included))

                if mode == "auto":
                    async with listen(
                        client.session, resources_list_changed=True
                    ) as events:
                        await change_membership(False)
                        assert isinstance(
                            await asyncio.wait_for(anext(events), 5),
                            ResourcesListChanged,
                        )
                        assert await client.list_resources() == []
                        await change_membership(True)
                        assert isinstance(
                            await asyncio.wait_for(anext(events), 5),
                            ResourcesListChanged,
                        )
                        assert len(await client.list_resources()) == 1
                        assert await app.state.server_manager.restart_server("alpha")
                        assert isinstance(
                            await asyncio.wait_for(anext(events), 5),
                            ResourcesListChanged,
                        )
                else:
                    await change_membership(False)
                    await wait_for(
                        lambda: recorder.methods.count(
                            "notifications/resources/list_changed"
                        )
                        == 1
                    )
                    assert await client.list_resources() == []
                    await change_membership(True)
                    await wait_for(
                        lambda: recorder.methods.count(
                            "notifications/resources/list_changed"
                        )
                        == 2
                    )
                    assert len(await client.list_resources()) == 1
                    before = recorder.methods.count(
                        "notifications/resources/list_changed"
                    )
                    assert await app.state.server_manager.restart_server("alpha")
                    await wait_for(
                        lambda: recorder.methods.count(
                            "notifications/resources/list_changed"
                        )
                        > before
                    )
                resources = await client.list_resources()
                assert len(resources) == 1
                assert str(resources[0].uri) != original_uri
                with pytest.raises(MCPError):
                    await client.read_resource(original_uri)
                assert await client.read_resource(str(resources[0].uri))
            await wait_for(lambda: bus.active == 0)
