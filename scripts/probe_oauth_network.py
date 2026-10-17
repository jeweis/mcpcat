"""真实 TCP 的只读授权探测：HTTP 自动协商与旧 SSE，不启动浏览器。"""

import asyncio
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.probe_fastmcp_network import serve


async def check_network() -> None:
    """仅创建临时回环夹具，不加载用户应用/配置/数据库。"""
    from app.services.oauth_probe import probe_mcp_auth

    source = FastMCP("auth-network-fixture")
    report = []
    for transport in ("http", "sse"):
        async with serve(source, transport) as url:
            result = await probe_mcp_auth(url, server_type=transport)
            expected = "2026-07-28" if transport == "http" else "2025-11-25"
            assert result.protocol_version == expected
            assert result.challenge is None
            report.append(
                {
                    "transport": transport,
                    "protocol": result.protocol_version,
                    "result": "passed",
                }
            )
    print(json.dumps(report, indent=2))


async def main() -> None:
    """在临时 cwd 才导入 app，避免隐式读取工作区 .env。"""
    previous = os.getcwd()
    with TemporaryDirectory(prefix="mcpcat-oauth-network-") as root:
        os.chdir(root)
        try:
            await check_network()
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    asyncio.run(main())
