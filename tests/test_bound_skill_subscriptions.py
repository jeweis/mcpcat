"""真实来源与 Manager 网关：来源事件和附带 Skill 事务通知合并。"""

import asyncio

import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.dependencies import get_http_request
from mcp.client.subscriptions import listen
from mcp.server.subscriptions import ListenHandler
from mcp.shared.subscriptions import (
    ResourceUpdated,
    ResourcesListChanged,
    ToolsListChanged,
)
from test_gateway_client_capabilities import live_http
from test_proxy_subscriptions import CountingBus, ListRecorder, wait_for
from test_skill_content_workflow import READ, content_app
from test_skills_source_gateway import publish_bound
from test_subscription_public_adapter import PublicListenAdapter

from app.application import create_app
from app.services.config_service import ConfigService
from app.services.inspector_service import inspector_service
from app.services.market_service import MarketService
from app.services.server_manager import MCPServerManager


class RecordingAdapter(PublicListenAdapter):
    def __init__(self, handler, requests):
        super().__init__(handler)
        self.requests = requests

    async def on_request(self, context, call_next):
        if context.method == "subscriptions/listen":
            self.requests.append(await get_http_request().json())
        return await super().on_request(context, call_next)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_source_and_bound_skill_events_share_gateway_endpoint(
    content_app, monkeypatch, mode
):
    _, database = content_app
    bus, requests = CountingBus(), []
    source = FastMCP(
        "source", middleware=[RecordingAdapter(ListenHandler(bus), requests)]
    )

    @source.tool()
    def echo(message: str) -> str:
        return message

    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    ConfigService.update_setting_section("catalog", {"enabled": False})
    async with asyncio.timeout(40), live_http(source.http_app(path="/mcp")) as upstream:
        assert ConfigService.add_server_to_config(
            "alpha",
            {
                "type": "streamable-http",
                "url": upstream,
                "require_auth": True,
            },
        )
        app = create_app(database=database, server_manager=MCPServerManager())
        async with live_http(app) as url:
            recorder = ListRecorder()
            transport = StreamableHttpTransport(
                url + "/alpha/", headers={"Mcpcat-Key": READ}
            )
            async with Client(transport, mode=mode, message_handler=recorder) as client:
                if mode == "legacy":
                    assert await client.list_resources() == []
                    await wait_for(lambda: bus.active == 1)
                    publish_bound(database)
                    await wait_for(
                        lambda: "notifications/resources/list_changed"
                        in recorder.methods
                    )
                    uri = str((await client.list_resources())[0].uri)
                    await client.session.subscribe_resource(uri)
                    source.disable(names={"echo"}, components={"tool"})
                    await bus.publish(ToolsListChanged())
                    await wait_for(
                        lambda: "notifications/tools/list_changed" in recorder.methods
                    )
                    await wait_for(
                        lambda: recorder.methods.count(
                            "notifications/resources/list_changed"
                        )
                        == 2
                    )
                    assert await client.list_resources() == []
                    await client.session.unsubscribe_resource(uri)
                else:
                    async with listen(
                        client.session,
                        resources_list_changed=True,
                        tools_list_changed=True,
                    ) as events:
                        publish_bound(database)
                        assert isinstance(
                            await asyncio.wait_for(anext(events), 5),
                            ResourcesListChanged,
                        )
                        uri = str((await client.list_resources())[0].uri)
                        async with listen(
                            client.session,
                            resource_subscriptions=[uri, "data://upstream"],
                        ) as updates:
                            assert set(updates.honored.resource_subscriptions) == {
                                uri,
                                "data://upstream",
                            }
                            pending = asyncio.create_task(anext(updates))
                            try:
                                await bus.publish(ResourceUpdated(uri))
                                await asyncio.sleep(0.1)
                                assert not pending.done(), "来源不能伪造平台 Skill 更新"
                                await bus.publish(ResourceUpdated("data://upstream"))
                                assert (
                                    await asyncio.wait_for(pending, 5)
                                ).uri == "data://upstream"
                            finally:
                                pending.cancel()
                                await asyncio.gather(pending, return_exceptions=True)
                        source.disable(names={"echo"}, components={"tool"})
                        await bus.publish(ToolsListChanged())
                        assert isinstance(
                            await asyncio.wait_for(anext(events), 5), ToolsListChanged
                        )
                        assert isinstance(
                            await asyncio.wait_for(anext(events), 5),
                            ResourcesListChanged,
                        )
                        assert await client.list_resources() == []
                assert requests
                assert all(uri not in str(request["params"]) for request in requests)
            await wait_for(lambda: bus.active == 0)


@pytest.mark.asyncio
async def test_platform_notifications_survive_source_without_listen(
    content_app, monkeypatch
):
    _, database = content_app
    source = FastMCP("no listen")

    @source.tool()
    def echo(message: str) -> str:
        return message

    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    ConfigService.update_setting_section("catalog", {"enabled": False})
    async with asyncio.timeout(30), live_http(source.http_app(path="/mcp")) as upstream:
        assert ConfigService.add_server_to_config(
            "alpha",
            {
                "type": "streamable-http",
                "url": upstream,
                "require_auth": True,
            },
        )
        app = create_app(database=database, server_manager=MCPServerManager())
        async with live_http(app) as url:
            transport = StreamableHttpTransport(
                url + "/alpha/", headers={"Mcpcat-Key": READ}
            )
            async with Client(transport) as client:
                async with listen(
                    client.session,
                    resources_list_changed=True,
                    tools_list_changed=True,
                    resource_subscriptions=["data://unsupported"],
                ) as events:
                    assert events.honored.resources_list_changed is True
                    assert not events.honored.tools_list_changed
                    assert not events.honored.resource_subscriptions
                    publish_bound(database)
                    assert isinstance(
                        await asyncio.wait_for(anext(events), 5), ResourcesListChanged
                    )
                    assert (
                        await client.call_tool("echo", {"message": "still works"})
                    ).data == "still works"
