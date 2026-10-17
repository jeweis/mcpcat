"""按真实旧协议日期验证 HTTP 网关，不以 mode 名称代替协商证据。"""

import asyncio
import json

import httpx2
import pytest
from fastmcp import Context, FastMCP
from tests.test_gateway_client_capabilities import live_http
from tests.test_proxy_cross_generation import single_era

from app.services.mcp_factory import MCPServerFactory


def response_message(response, request_id):
    """测试端解析完整 JSON 或 SSE 响应，匹配原始请求 ID。"""
    response.raise_for_status()
    if "application/json" in response.headers.get("content-type", ""):
        return response.json()
    for event in response.text.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
        if data:
            value = json.loads(data)
            if value.get("id") == request_id:
                return value
    raise AssertionError("Missing matching JSON-RPC response")


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["2025-03-26", "2025-06-18", "2025-11-25"])
@pytest.mark.parametrize("era", ["modern", "legacy"])
async def test_gateway_exact_legacy_date_components(version, era):
    """旧客户端三日期到两代来源，覆盖工具、资源、提示及标准错误。"""
    source = FastMCP("dated source")

    @source.tool()
    def echo(value: str, ctx: Context) -> dict:
        return {"value": value, "protocol": ctx.request_context.protocol_version}

    @source.resource("fixture://guide")
    def guide() -> str:
        return "legacy resource"

    @source.prompt()
    def greeting(name: str) -> str:
        return "hello " + name

    app = single_era(source.http_app(path="/mcp", json_response=True), era, [])
    async with asyncio.timeout(30), live_http(app) as upstream:
        gateway = MCPServerFactory.create_server("dated", {
            "type": "streamable-http", "url": upstream,
        })
        async with live_http(gateway.http_app(path="/mcp", json_response=True)) as endpoint:
            async with httpx2.AsyncClient(timeout=15) as client:
                headers = {"Accept": "application/json, text/event-stream"}
                next_id = 0

                async def rpc(method, params):
                    nonlocal next_id
                    next_id += 1
                    response = await client.post(endpoint, headers=headers, json={
                        "jsonrpc": "2.0", "id": next_id, "method": method, "params": params,
                    })
                    if response.headers.get("mcp-session-id"):
                        headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
                    return response_message(response, next_id)

                init = await rpc("initialize", {"protocolVersion": version, "capabilities": {},
                    "clientInfo": {"name": "dated-client", "version": "1"}})
                assert init["result"]["protocolVersion"] == version
                headers["MCP-Protocol-Version"] = version
                ready = await client.post(endpoint, headers=headers,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"})
                assert ready.status_code == 202
                tools = (await rpc("tools/list", {}))["result"]["tools"]
                assert tools[0]["name"] == "echo"
                result = (await rpc("tools/call", {"name": "echo", "arguments": {"value": "ok"}}))["result"]
                text = json.loads(result["content"][0]["text"])
                assert text == {"value": "ok", "protocol": "2026-07-28" if era == "modern" else "2025-11-25"}
                assert (await rpc("resources/list", {}))["result"]["resources"][0]["uri"] == "fixture://guide"
                assert (await rpc("resources/read", {"uri": "fixture://guide"}))["result"]["contents"][0]["text"] == "legacy resource"
                assert (await rpc("prompts/list", {}))["result"]["prompts"][0]["name"] == "greeting"
                message = (await rpc("prompts/get", {"name": "greeting", "arguments": {"name": "Alice"}}))["result"]["messages"][0]
                assert message["content"]["text"] == "hello Alice"
                assert (await rpc("fixture/unknown", {}))["error"]["code"] == -32601
                await client.delete(endpoint, headers=headers)
