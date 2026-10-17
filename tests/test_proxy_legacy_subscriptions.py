"""新版下游订阅旧版来源，验证真实通知及取消订阅。"""

import asyncio

import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware
from mcp.client.subscriptions import listen
from mcp.shared.subscriptions import ResourceUpdated, ToolsListChanged
from mcp.shared.exceptions import MCPError
from mcp.types import EmptyResult, ResourcesCapability, ToolsCapability
from test_gateway_client_capabilities import live_http
from test_proxy_cross_generation import single_era
from test_proxy_subscriptions import wait_for

from app.services.proxy_clients import make_proxy_client_factory
from app.services.proxy_subscriptions import ProxySubscriptions


class LegacySource(Middleware):
    def __init__(self, fail_uri=None):
        self.subscribed = {}
        self.removed = []
        self.fail_uri = fail_uri

    async def on_initialize(self, context, call_next):
        result = await call_next(context)
        result.capabilities.resources = ResourcesCapability(
            subscribe=True, list_changed=True
        )
        result.capabilities.tools = ToolsCapability(list_changed=True)
        return result

    async def on_request(self, context, call_next):
        if context.method in {"resources/subscribe", "resources/unsubscribe"}:
            message = context.message
            uri = message["uri"] if isinstance(message, dict) else message.uri
            if context.method == "resources/subscribe":
                if uri == self.fail_uri:
                    raise MCPError(-32602, "Fixture resource unavailable")
                self.subscribed[uri] = context.fastmcp_context.session
            else:
                self.subscribed.pop(uri, None)
                self.removed.append(uri)
            return EmptyResult()
        return await call_next(context)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
async def test_modern_client_receives_legacy_events_and_unsubscribes(transport):
    fixture = LegacySource()
    source = FastMCP("legacy source", middleware=[fixture])
    app = source.http_app(path="/mcp", json_response=False, transport=transport)
    wire = []
    application = (
        single_era(app, "legacy", wire) if transport == "streamable-http" else app
    )
    async with asyncio.timeout(20), live_http(application) as upstream:
        factory = make_proxy_client_factory({"type": transport, "url": upstream})
        adapter = ProxySubscriptions(factory, lambda request: True)
        gateway = FastMCP("gateway", middleware=[adapter])
        async with live_http(gateway.http_app(path="/mcp")) as endpoint:
            async with Client(endpoint) as client:
                async with listen(
                    client.session,
                    tools_list_changed=True,
                    resource_subscriptions=["data://one"],
                ) as stream:
                    assert stream.honored.tools_list_changed is True
                    assert stream.honored.resource_subscriptions == ["data://one"]
                    session = fixture.subscribed["data://one"]
                    await session.send_resource_updated("data://other")
                    await session.send_resource_updated("data://one")
                    await session.send_tool_list_changed()
                    resource = await anext(stream)
                    assert (
                        isinstance(resource, ResourceUpdated)
                        and resource.uri == "data://one"
                    )
                    assert isinstance(await anext(stream), ToolsListChanged)
                await wait_for(lambda: not fixture.subscribed and adapter.active == 0)
    assert fixture.removed == ["data://one"]
    if transport == "streamable-http":
        assert "initialize" in wire


@pytest.mark.asyncio
async def test_partial_legacy_subscription_failure_cleans_successful_resources():
    fixture = LegacySource(fail_uri="data://missing")
    source = FastMCP("legacy source", middleware=[fixture])
    app = source.http_app(path="/mcp", json_response=False)
    async with asyncio.timeout(20), live_http(
        single_era(app, "legacy", [])
    ) as upstream:
        factory = make_proxy_client_factory(
            {"type": "streamable-http", "url": upstream}
        )
        adapter = ProxySubscriptions(factory, lambda request: True)
        gateway = FastMCP("gateway", middleware=[adapter])
        async with live_http(gateway.http_app(path="/mcp")) as endpoint:
            async with Client(endpoint) as client:
                with pytest.raises(MCPError):
                    async with listen(
                        client.session,
                        resource_subscriptions=["data://one", "data://missing"],
                    ):
                        pytest.fail("部分失败不能确认完整订阅")
                await wait_for(lambda: not fixture.subscribed and adapter.active == 0)
    assert fixture.removed == ["data://one"]
