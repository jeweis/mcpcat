"""能力收窄必须覆盖真实上下游两种协议，且不改写共享消息。"""

import asyncio
import os
import sys
from pathlib import Path

import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.client.transports import infer_transport
from mcp.shared.message import ClientMessageMetadata, SessionMessage
from mcp_types import (
    CLIENT_CAPABILITIES_META_KEY,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
)
from test_gateway_client_capabilities import live_http
from test_proxy_cross_generation import single_era

from app.services.mcp_factory import MCPServerFactory
from app.services import mcp_factory
from app.services.proxy_clients import make_proxy_client_factory
from app.services.proxy_capability_transport import (
    CapabilitySendStream,
    CapabilityTransport,
)


@pytest.mark.asyncio
async def test_advertisement_copy_preserves_metadata_and_other_capabilities():
    sent = []

    class Sink:
        async def send(self, item):
            sent.append(item)

    capabilities = {"elicitation": {"form": {}, "url": {}}, "sampling": {"tools": {}}}
    params = {"_meta": {CLIENT_CAPABILITIES_META_KEY: capabilities, "custom": "keep"}}
    item = SessionMessage(
        JSONRPCRequest(jsonrpc="2.0", id=7, method="tools/list", params=params),
        ClientMessageMetadata(headers={"Mcp-Method": "tools/list"}),
    )
    await CapabilitySendStream(Sink(), {"form": {}}).send(item)
    assert sent[0].metadata is item.metadata
    assert sent[0].message.id == 7
    assert sent[0].message.params["_meta"][CLIENT_CAPABILITIES_META_KEY] == {
        "elicitation": {"form": {}},
        "sampling": {"tools": {}},
    }
    assert item.message.params["_meta"][CLIENT_CAPABILITIES_META_KEY] == capabilities
    assert capabilities["elicitation"] == {"form": {}, "url": {}}


@pytest.mark.asyncio
@pytest.mark.parametrize("watching", [True, False])
@pytest.mark.parametrize("method", ["initialize", "tools/list"])
async def test_roots_notification_advertisement_matches_protocol_and_route(
    watching, method
):
    sent = []

    class Sink:
        async def send(self, item):
            sent.append(item)

    capability = {"roots": {"listChanged": True}}
    params = (
        {"capabilities": capability}
        if method == "initialize"
        else {"_meta": {CLIENT_CAPABILITIES_META_KEY: capability}}
    )
    await CapabilitySendStream(Sink(), None, watching).send(
        SessionMessage(
            JSONRPCRequest(jsonrpc="2.0", id=1, method=method, params=params)
        )
    )
    result = sent[0].message.params
    result = (
        result["capabilities"]
        if method == "initialize"
        else result["_meta"][CLIENT_CAPABILITIES_META_KEY]
    )
    assert result["roots"]["listChanged"] is (watching and method == "initialize")
    assert capability["roots"]["listChanged"] is True


@pytest.mark.asyncio
async def test_non_requests_are_forwarded_unchanged_and_stream_is_closed():
    sent, closed = [], []

    class Sink:
        async def send(self, item):
            sent.append(item)

        async def aclose(self):
            closed.append(True)

    stream = CapabilitySendStream(Sink(), None)
    for message in (
        JSONRPCNotification(jsonrpc="2.0", method="notifications/initialized"),
        JSONRPCResponse(jsonrpc="2.0", id=8, result={"action": "decline"}),
    ):
        item = SessionMessage(message)
        await stream.send(item)
        assert sent[-1] is item
    await stream.aclose()
    assert closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream", ["modern", "legacy", "sse"])
@pytest.mark.parametrize("downstream", ["auto", "legacy"])
@pytest.mark.parametrize(
    "allowed", [None, {"form": {}}, {"url": {}}, {"form": {}, "url": {}}]
)
async def test_exact_input_advertisement_reaches_upstream(
    upstream, downstream, allowed
):
    source = FastMCP("capability fixture")

    @source.tool()
    async def inspect_capabilities(ctx: Context) -> dict:
        capability = ctx.session.client_capabilities.elicitation
        return {
            "elicitation": (
                capability.model_dump(by_alias=True, exclude_none=True)
                if capability
                else None
            ),
            "protocol": ctx.request_context.protocol_version,
        }

    async def answer(*args):
        raise AssertionError("Listing capabilities must not ask for user input")

    application = (
        source.http_app(path="/mcp", transport="sse")
        if upstream == "sse"
        else single_era(source.http_app(path="/mcp"), upstream, [])
    )
    async with asyncio.timeout(20), live_http(application) as target:
        proxy = MCPServerFactory.create_server(
            "capabilities",
            {
                "type": "sse" if upstream == "sse" else "streamable-http",
                "url": target,
                "timeout": 5,
            },
        )
        async with live_http(proxy.http_app(path="/mcp")) as url:
            transport = CapabilityTransport(infer_transport(url), allowed)
            async with Client(
                transport, mode=downstream, elicitation_handler=answer
            ) as client:
                result = await client.call_tool("inspect_capabilities")
                assert client.protocol_version == (
                    "2026-07-28" if downstream == "auto" else "2025-11-25"
                )
                assert result.data == {
                    "elicitation": allowed,
                    "protocol": "2026-07-28" if upstream == "modern" else "2025-11-25",
                }


@pytest.mark.asyncio
@pytest.mark.parametrize("downstream", ["auto", "legacy"])
@pytest.mark.parametrize("allowed", [{"form": {}}, {"url": {}}])
async def test_stdio_capabilities_and_process_release(monkeypatch, downstream, allowed):
    clients = []

    def tracked_factory(config):
        factory = make_proxy_client_factory(config)

        def create(**kwargs):
            client = factory(**kwargs)
            clients.append(client)
            return client

        return create

    monkeypatch.setattr(mcp_factory, "make_proxy_client_factory", tracked_factory)
    proxy = MCPServerFactory.create_server(
        "stdio",
        {
            "type": "stdio",
            "command": sys.executable,
            "args": [
                str(Path(__file__).parent / "fixtures" / "mcp_stdio_capabilities.py")
            ],
            "timeout": 10,
        },
    )

    async def answer(*args):
        raise AssertionError("No input requested")

    try:
        async with asyncio.timeout(30), live_http(proxy.http_app(path="/mcp")) as url:
            async with Client(
                CapabilityTransport(infer_transport(url), allowed),
                mode=downstream,
                elicitation_handler=answer,
            ) as client:
                result = await client.call_tool("inspect_capabilities")
                assert result.data["elicitation"] == allowed
                assert result.data["protocol"] == "2026-07-28"
                with pytest.raises(ProcessLookupError):
                    os.kill(result.data["pid"], 0)
    finally:
        for client in clients:
            await client.close()
