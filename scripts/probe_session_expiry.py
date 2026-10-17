"""有限空闲期限与显式重连的真实 TCP 验证；不重放失败的业务调用。"""

import asyncio
import json
import sys
from pathlib import Path

import httpx2
from fastmcp import Client, FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.probe_fastmcp_network import serve


async def initialize(client: httpx2.AsyncClient, url: str) -> str:
    """真实旧握手，不启动持续 GET，以便进入无请求在途的空闲状态。"""
    response = await client.post(
        url,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "idle-fixture", "version": "1"},
            },
        },
    )
    response.raise_for_status()
    session = response.headers["mcp-session-id"]
    client.headers.update(
        {"Mcp-Session-Id": session, "Mcp-Protocol-Version": "2025-11-25"}
    )
    await client.post(
        url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    return session


async def call(client: httpx2.AsyncClient, url: str, value: str) -> httpx2.Response:
    """每次显式调用只发送一次 POST，不自动重试。"""
    return await client.post(
        url,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"value": value}},
        },
    )


async def check_legacy(url: str, timeout: float | None, calls: list) -> None:
    """旧会话超时返回404，只有显式新握手才恢复后续调用。"""
    async with httpx2.AsyncClient(
        headers={"Accept": "application/json, text/event-stream"}, timeout=3
    ) as client:
        session = await initialize(client, url)
        assert (await call(client, url, "before")).status_code == 200
        await asyncio.sleep(0.4)
        if timeout is None:
            assert (await call(client, url, "after")).status_code == 200
        else:
            response = await call(client, url, "not-replayed")
            assert response.status_code == 404, response.text
            assert "session" in response.text.lower()
            assert calls == ["before"]
            client.headers.pop("Mcp-Session-Id")
            assert await initialize(client, url) != session
            assert (await call(client, url, "reconnected")).status_code == 200


async def check_idle(timeout: float | None) -> dict:
    """验证旧无期限/有限期限，以及不靠 ping 保活的现代调用。"""
    server = FastMCP("idle-fixture")
    calls = []

    @server.tool()
    def echo(value: str) -> str:
        """记录已执行的夹具动作，用于证明失败请求未被重放。"""
        calls.append(value)
        return value

    async with serve(
        server, "http", session_idle_timeout=timeout, json_response=True
    ) as url:
        await check_legacy(url, timeout, calls)
        async with Client(url, mode="auto", timeout=3) as client:
            assert client.protocol_version == "2026-07-28"
            await asyncio.sleep(0.4)
            assert (
                await client.call_tool("echo", {"value": "modern"})
            ).data == "modern"
    return {"idle_timeout": timeout, "calls": calls, "result": "passed"}


async def main() -> None:
    """只在临时回环端口测试默认无期限和显式有限期限。"""
    print(json.dumps([await check_idle(None), await check_idle(0.1)], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
