"""真实 loopback 两跳代理与已发布 mcporter；不读取用户配置或数据库。"""

import asyncio
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from fastmcp import Client, Context, FastMCP

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.probe_fastmcp_network import serve


async def mcporter_call(url: str) -> None:
    """仅使用缓存固定版本和空配置，调用本机夹具，不访问用户 Profile。"""
    process = await asyncio.create_subprocess_exec(
        "npm",
        "exec",
        "--offline",
        "--yes",
        "--package=mcporter@0.13.7",
        "--",
        "mcporter",
        "--config",
        str(ROOT / "scripts/fixtures/mcporter-empty.json"),
        "call",
        f"{url.rstrip('/')}.echo",
        "value=passed",
        "--allow-http",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    if process.returncode or "passed" not in stdout.decode():
        raise RuntimeError("mcporter fixture failed: " + stderr.decode())


async def check_transport(source: FastMCP, transport: str) -> list[dict]:
    """真实 TCP 上游 HTTP/SSE，经实际工厂注册后再从 HTTP 网关进入。"""
    from app.services.mcp_factory import MCPServerFactory

    report = []
    async with serve(source, transport) as upstream:
        proxy = MCPServerFactory.create_server(
            "fixture",
            {
                "type": "sse" if transport == "sse" else "streamable-http",
                "url": upstream,
                "timeout": 5,
            },
        )
        async with serve(proxy, "http") as gateway:
            expected = "2025-11-25" if transport == "sse" else "2026-07-28"
            for mode in ("auto", "legacy"):
                async with Client(gateway, mode=mode) as client:
                    result = await client.call_tool("echo", {"value": "passed"})
                    assert result.data == {"value": "passed", "protocol": expected}
                    report.append(
                        {
                            "upstream": transport,
                            "client": mode,
                            "frontend_protocol": client.protocol_version,
                            "upstream_protocol": result.data["protocol"],
                        }
                    )
            await mcporter_call(gateway)
            report.append(
                {"upstream": transport, "client": "mcporter@0.13.7", "result": "passed"}
            )
    return report


async def main() -> None:
    """所有 app 模块导入都在临时 cwd，现有 .env/SQLite/制品均不使用。"""
    previous = os.getcwd()
    with TemporaryDirectory(prefix="mcpcat-proxy-network-") as root:
        os.chdir(root)
        try:
            source = FastMCP("proxy-network-fixture")

            @source.tool()
            def echo(value: str, ctx: Context) -> dict:
                return {
                    "value": value,
                    "protocol": ctx.request_context.protocol_version,
                }

            report = []
            for transport in ("http", "sse"):
                report.extend(await check_transport(source, transport))
            print(json.dumps(report, indent=2))
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    asyncio.run(main())
