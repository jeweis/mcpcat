"""固定框架版本的公开订阅接入探针，不替代实际网关端到端验收。"""

import asyncio
import json

import httpx2
import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import Middleware
from mcp.client.subscriptions import listen
from mcp.server.context import ServerRequestContext
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler
from mcp.shared.subscriptions import ToolsListChanged
from mcp.types import SubscriptionsListenRequestParams
from test_gateway_client_capabilities import live_http


class PublicListenAdapter(Middleware):
    """只验证公开 HTTP 请求、公开上下文与 SDK handler 可以组合。"""

    def __init__(self, handler):
        self.handler = handler

    async def on_request(self, context, call_next):
        if context.method != "subscriptions/listen":
            return await call_next(context)
        request = get_http_request()
        envelope = await request.json()
        rc = context.fastmcp_context.request_context
        sdk_context = ServerRequestContext(
            session=rc.session,
            lifespan_context=rc.lifespan_context,
            protocol_version=rc.protocol_version,
            method=context.method,
            request_id=envelope["id"],
            request=request,
        )
        return await self.handler(
            sdk_context,
            SubscriptionsListenRequestParams.model_validate(envelope["params"]),
        )


@pytest.mark.asyncio
async def test_public_http_middleware_can_serve_sdk_listen():
    bus = InMemorySubscriptionBus()
    handler = ListenHandler(bus)
    server = FastMCP("public listen adapter", middleware=[PublicListenAdapter(handler)])
    async with asyncio.timeout(15), live_http(server.http_app(path="/mcp")) as url:
        async with Client(url) as client:
            async with listen(client.session, tools_list_changed=True) as stream:
                assert stream.honored.tools_list_changed is True
                await bus.publish(ToolsListChanged())
                event = await anext(stream)
                assert isinstance(event, ToolsListChanged)
                handler.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("request_id", [7, "7"])
async def test_subscription_ack_and_result_preserve_request_id_type(request_id):
    handler = ListenHandler(InMemorySubscriptionBus())
    server = FastMCP("id fixture", middleware=[PublicListenAdapter(handler)])
    envelope = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "subscriptions/listen",
        "params": {
            "notifications": {"toolsListChanged": True},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }
    async with asyncio.timeout(15), live_http(server.http_app(path="/mcp")) as url:
        async with httpx2.AsyncClient() as client:
            async with client.stream(
                "POST",
                url,
                json=envelope,
                headers={
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "subscriptions/listen",
                },
            ) as response:
                if response.status_code != 200:
                    pytest.fail((await response.aread()).decode())
                frames = []
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    frame = json.loads(line[6:])
                    frames.append(frame)
                    if "method" in frame:
                        assert (
                            frame["params"]["_meta"][
                                "io.modelcontextprotocol/subscriptionId"
                            ]
                            == request_id
                        )
                        assert type(
                            frame["params"]["_meta"][
                                "io.modelcontextprotocol/subscriptionId"
                            ]
                        ) is type(request_id)
                        handler.close()
                assert frames[-1]["id"] == request_id
                assert type(frames[-1]["id"]) is type(request_id)
                assert "result" in frames[-1]
