"""输入恢复前拒绝撤权和超大回答，不执行上游后续业务步骤。"""

import asyncio

import pytest
from fastmcp import Client
from mcp.shared.exceptions import MCPError
from test_gateway_client_capabilities import live_http
from test_proxy_cross_generation import single_era
from test_sampling_modern_roundtrip import sampling_source

from app.services.legacy_input_bridge import LegacyInputBridge
from app.services.mcp_factory import MCPServerFactory


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["revoked", "oversize", "revoked-waiting"])
async def test_input_rejected_before_continuation(failure):
    calls = []
    allowed = True
    source = sampling_source(calls)
    closed = asyncio.Event()

    async def answer(messages, params, context):
        nonlocal allowed
        if failure.startswith("revoked"):
            allowed = False
        if failure == "revoked-waiting":
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
        return "x" * (70000 if failure == "oversize" else 1)

    http = source.http_app(path="/mcp", json_response=False)
    async with asyncio.timeout(20), live_http(single_era(http, "modern", [])) as target:
        proxy = MCPServerFactory.create_server(
            "sampling", {"type": "streamable-http", "url": target, "timeout": 5}
        )
        bridge = next(
            mw for mw in proxy.middleware if isinstance(mw, LegacyInputBridge)
        )
        bridge.authorized = lambda request: allowed
        async with live_http(proxy.http_app(path="/mcp")) as url:
            async with Client(url, mode="legacy", sampling_handler=answer) as client:
                error = (
                    "access denied"
                    if failure.startswith("revoked")
                    else "response limit"
                )
                with pytest.raises(MCPError, match=error):
                    await client.call_tool("sample")
                if failure == "revoked-waiting":
                    await asyncio.wait_for(closed.wait(), 3)
    assert calls == []
