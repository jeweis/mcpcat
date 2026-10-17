"""真实上游→订阅适配层→客户端，验证筛选、撤权和连接释放。"""

import asyncio

import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from fastmcp.client.messages import MessageHandler
from mcp.client.subscriptions import listen
from mcp.server.mcpserver import MCPServer
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler
from mcp.shared.subscriptions import (
    ResourceUpdated,
    ToolsListChanged,
    ResourcesListChanged,
    PromptsListChanged,
)
from test_gateway_client_capabilities import live_http
from test_subscription_public_adapter import PublicListenAdapter

from app.services.proxy_subscriptions import ProxySubscriptions
from app.services.server_manager import MCPServerManager
from app.services.security_service import security_service


class CountingBus(InMemorySubscriptionBus):
    def __init__(self):
        super().__init__()
        self.active = 0

    def subscribe(self, listener):
        release = super().subscribe(listener)
        self.active += 1
        released = False

        def unsubscribe():
            nonlocal released
            if not released:
                released = True
                self.active -= 1
                release()

        return unsubscribe


async def wait_for(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_modern_source_isolated_filters_and_revocation():
    bus = CountingBus()
    source = MCPServer("source", subscriptions=bus)
    permitted = True
    async with asyncio.timeout(20), live_http(source.streamable_http_app()) as upstream:
        adapter = ProxySubscriptions(
            lambda **options: Client(upstream, **options), lambda request: permitted
        )
        gateway = FastMCP("gateway", middleware=[adapter])
        async with live_http(gateway.http_app(path="/mcp")) as endpoint:
            async with Client(endpoint) as alice, Client(endpoint) as bob:
                async with listen(
                    alice.session, resource_subscriptions=["data://alice"]
                ) as a:
                    async with listen(
                        bob.session, resource_subscriptions=["data://bob"]
                    ) as b:
                        assert bus.active == adapter.active == 2
                        await bus.publish(ResourceUpdated("data://alice"))
                        await bus.publish(ResourceUpdated("data://bob"))
                        assert (await anext(a)).uri == "data://alice"
                        assert (await anext(b)).uri == "data://bob"
                        assert a.honored.resource_subscriptions == ["data://alice"]
                        permitted = False
                        await wait_for(lambda: adapter.active == 0 and bus.active == 0)
            assert bus.active == adapter.active == 0


@pytest.mark.asyncio
async def test_stop_downstream_releases_upstream_subscription():
    bus = CountingBus()
    source = MCPServer("source", subscriptions=bus)
    async with asyncio.timeout(20), live_http(source.streamable_http_app()) as upstream:
        adapter = ProxySubscriptions(
            lambda **options: Client(upstream, **options), lambda request: True
        )
        gateway = FastMCP("gateway", middleware=[adapter])
        async with live_http(gateway.http_app(path="/mcp")) as endpoint:
            async with Client(endpoint) as client:
                async with listen(client.session, tools_list_changed=True):
                    assert bus.active == adapter.active == 1
                await wait_for(lambda: adapter.active == 0 and bus.active == 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoke", "replace", "stop"])
@pytest.mark.parametrize("require_auth", [False, True])
async def test_manager_wiring_enforces_key_and_source_lifecycle(
    monkeypatch, change, require_auth
):
    bus = CountingBus()
    source = MCPServer("managed source", subscriptions=bus)
    permitted = True
    monkeypatch.setattr(
        security_service, "get_auth_header_name", lambda: "X-Fixture-Key"
    )
    monkeypatch.setattr(
        security_service,
        "verify_api_key",
        lambda key: object() if key == "fixture-only" and permitted else None,
    )
    monkeypatch.setattr(
        security_service, "has_permission", lambda identity, permission: True
    )
    async with asyncio.timeout(20), live_http(source.streamable_http_app()) as upstream:
        manager = MCPServerManager()
        assert manager.add_mcp_server(
            "source",
            {
                "type": "streamable-http",
                "url": upstream,
                "require_auth": require_auth,
            },
        )
        info = manager.server_info["source"]
        info["status"] = "running"
        async with live_http(info["mcp_app"]) as endpoint:
            # manager 使用根路径应用，live_http 返回的 /mcp 要改为 /。
            endpoint = endpoint.removesuffix("mcp")
            async with Client(endpoint) as denied:
                if require_auth:
                    with pytest.raises(Exception, match="Subscription access denied"):
                        async with listen(denied.session, tools_list_changed=True):
                            pytest.fail("未提供凭证不能订阅")
                else:
                    async with listen(denied.session, tools_list_changed=True):
                        assert bus.active == 1
            await wait_for(lambda: bus.active == 0)
            transport = StreamableHttpTransport(
                endpoint, headers={"X-Fixture-Key": "fixture-only"}
            )
            async with Client(transport) as client:
                async with listen(client.session, tools_list_changed=True):
                    assert bus.active == 1
                    if change == "revoke":
                        permitted = False
                    elif change == "replace":
                        info["mcp"] = object()
                    else:
                        info["status"] = "stopped"
                    await wait_for(lambda: bus.active == 0)


class ResourceRecorder(MessageHandler):
    def __init__(self):
        self.uris = []

    async def on_resource_updated(self, message):
        self.uris.append(str(message.params.uri))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ending", ["unsubscribe", "disconnect", "stop", "replace", "make_private"]
)
async def test_legacy_client_resource_subscription_lifecycle(ending):
    bus = CountingBus()
    source = MCPServer("modern source", subscriptions=bus)
    async with asyncio.timeout(20), live_http(source.streamable_http_app()) as upstream:
        manager = MCPServerManager()
        assert manager.add_mcp_server(
            "source",
            {
                "type": "streamable-http",
                "url": upstream,
                "require_auth": False,
            },
        )
        info = manager.server_info["source"]
        info["status"] = "running"
        try:
            async with live_http(info["mcp_app"]) as endpoint:
                recorder = ResourceRecorder()
                async with Client(
                    endpoint.removesuffix("mcp"),
                    mode="legacy",
                    message_handler=recorder,
                ) as client:
                    assert client.server_capabilities.resources.subscribe is True
                    await client.session.subscribe_resource("data://one")
                    await client.session.subscribe_resource("data://one")
                    assert bus.active == 1
                    assert len(manager.lifecycle.background) == 1
                    await bus.publish(ResourceUpdated("data://other"))
                    await bus.publish(ResourceUpdated("data://one"))
                    await wait_for(lambda: bool(recorder.uris))
                    assert recorder.uris == ["data://one"]
                    if ending == "unsubscribe":
                        await client.session.unsubscribe_resource("data://one")
                    elif ending == "stop":
                        assert await manager.lifecycle.stop("source")
                    elif ending == "replace":
                        info["mcp"] = object()
                    elif ending == "make_private":
                        info["config"]["require_auth"] = True
                    if ending != "disconnect":
                        await wait_for(
                            lambda: bus.active == 0 and not manager.lifecycle.background
                        )
                await wait_for(
                    lambda: bus.active == 0 and not manager.lifecycle.background
                )
        finally:
            await manager.lifecycle.cancel_background()


class ListRecorder(MessageHandler):
    def __init__(self):
        self.methods = []

    async def on_notification(self, message):
        if message.method.endswith("/list_changed"):
            self.methods.append(message.method)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "sse"])
async def test_legacy_list_changes_use_one_watcher_and_release_on_disconnect(transport):
    bus = CountingBus()
    source = FastMCP("source", middleware=[PublicListenAdapter(ListenHandler(bus))])

    @source.tool()
    def echo() -> str:
        return "fixture"

    async with asyncio.timeout(20), live_http(source.http_app(path="/mcp")) as upstream:
        manager = MCPServerManager()
        assert manager.add_mcp_server(
            "source",
            {
                "type": "streamable-http",
                "url": upstream,
                "require_auth": False,
            },
        )
        info = manager.server_info["source"]
        info["status"] = "running"
        application = info["mcp_app"] if transport == "http" else info["sse_app"]
        try:
            async with live_http(application) as endpoint:
                target = (
                    StreamableHttpTransport(endpoint.removesuffix("mcp"))
                    if transport == "http"
                    else SSETransport(endpoint.removesuffix("mcp"))
                )
                recorder = ListRecorder()
                async with Client(
                    target, mode="legacy", message_handler=recorder
                ) as client:
                    assert [tool.name for tool in await client.list_tools()] == ["echo"]
                    await wait_for(lambda: bus.active == 1)
                    await client.list_tools()
                    await client.list_resources()
                    await client.list_prompts()
                    assert bus.active == 1
                    await bus.publish(ToolsListChanged())
                    await bus.publish(ResourcesListChanged())
                    await bus.publish(PromptsListChanged())
                    await wait_for(lambda: len(recorder.methods) == 3)
                    assert set(recorder.methods) == {
                        "notifications/tools/list_changed",
                        "notifications/resources/list_changed",
                        "notifications/prompts/list_changed",
                    }
                await wait_for(
                    lambda: bus.active == 0 and not manager.lifecycle.background
                )
        finally:
            await manager.lifecycle.cancel_background()


@pytest.mark.asyncio
async def test_unsupported_notifications_do_not_break_legacy_listing():
    source = FastMCP("no subscription handler")

    @source.tool()
    def echo() -> str:
        return "fixture"

    async with asyncio.timeout(20), live_http(source.http_app(path="/mcp")) as upstream:
        manager = MCPServerManager()
        assert manager.add_mcp_server(
            "source",
            {
                "type": "streamable-http",
                "url": upstream,
                "require_auth": False,
            },
        )
        info = manager.server_info["source"]
        info["status"] = "running"
        try:
            async with live_http(info["mcp_app"]) as endpoint:
                async with Client(
                    endpoint.removesuffix("mcp"), mode="legacy"
                ) as client:
                    assert [tool.name for tool in await client.list_tools()] == ["echo"]
                    await wait_for(lambda: not manager.lifecycle.background)
                    assert (await client.call_tool("echo")).data == "fixture"
        finally:
            await manager.lifecycle.cancel_background()
