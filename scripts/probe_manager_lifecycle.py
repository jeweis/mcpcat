"""真实 TCP 生命周期验证，使用临时 SQLite 与本机 FastMCP，不运行用户命令。"""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastmcp import Client, FastMCP
from fastmcp.client.transports import SSETransport

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.probe_fastmcp_network import serve_app

CONFIG = {"type": "stdio", "command": "fixture-not-executed", "args": []}


def source_factory(evidence: list):
    """替换用户程序启动边界，其后 FastMCP/HTTP/SSE 都使用真实实现。"""

    def create(name: str, config: dict) -> FastMCP:
        generation = config.get("note", "before")

        @asynccontextmanager
        async def lifespan(server):
            task = asyncio.current_task()
            evidence.append((generation, "enter", task))
            try:
                yield
            finally:
                evidence.append((generation, "exit", asyncio.current_task()))

        source = FastMCP(name, lifespan=lifespan)

        @source.tool()
        def identity() -> str:
            """返回当前代际标记。"""
            return generation

        return source

    return create


async def requests(root: str, expected: str) -> list[dict]:
    """现代/旧 HTTP 与旧 SSE 都从网关实际路由进入。"""
    report = []
    for transport, mode in (("http", "auto"), ("http", "legacy"), ("sse", "legacy")):
        target = (
            SSETransport(root + "/sse/fixture/")
            if transport == "sse"
            else root + "/mcp/fixture/"
        )
        async with Client(target, mode=mode, timeout=5) as client:
            assert (await client.call_tool("identity", {})).data == expected
            report.append(
                {
                    "generation": expected,
                    "transport": transport,
                    "protocol": client.protocol_version,
                    "result": "passed",
                }
            )
    return report


async def check_lifecycle(directory: str) -> None:
    """导入 app 前已切换 cwd，所有数据路径使用临时目录。"""
    from app.core.config import settings
    from app.services.config_service import ConfigService
    from app.services.mcp_factory import MCPServerFactory
    from app.services.server_manager import MCPServerManager
    from app.storage.database import Database
    from app.storage.migrations import upgrade_schema

    settings.mcpcat_backups_path = str(Path(directory) / "backups")
    database = Database(Path(directory) / "probe.db")
    upgrade_schema(database)
    ConfigService.configure_database(database)
    evidence, report = [], []
    try:
        with patch.object(MCPServerFactory, "create_server", source_factory(evidence)):
            assert ConfigService.add_server_to_config("fixture", CONFIG)
            manager = MCPServerManager()
            manager.load_servers_from_config()
            app = FastAPI(lifespan=manager.create_unified_lifespan)
            manager.mount_all_servers(app)
            async with serve_app(app) as root:
                report.extend(await requests(root, "before"))
                async with Client(
                    SSETransport(root + "/sse/fixture/"), mode="legacy", timeout=5
                ) as old:
                    assert (await old.call_tool("identity", {})).data == "before"
                    assert await manager.stop_server("fixture")
                    assert not manager.lifecycle.requests
                assert await manager.start_server("fixture")
                report.extend(await requests(root, "before"))
                assert await manager.restart_server(
                    "fixture", {**CONFIG, "note": "after"}
                )
                report.extend(await requests(root, "after"))
                assert await manager.remove_server("fixture")
                assert await manager.add_and_mount_server(
                    app, "fixture", {**CONFIG, "note": "readded"}
                )
                report.extend(await requests(root, "readded"))
            assert not manager.lifecycle.handles and not manager.dynamic_tasks
            assert not manager.lifecycle.requests and not manager.lifecycle.background
            assert len(evidence) == 8
            for begin, end in zip(evidence[::2], evidence[1::2]):
                assert begin[:2] == (end[0], "enter") and end[1] == "exit"
                assert begin[2] is end[2]
            print(json.dumps(report, indent=2))
    finally:
        ConfigService.reset_database()
        database.dispose()


async def main() -> None:
    """只在临时工作目录及本机随机端口验证。"""
    previous = os.getcwd()
    with TemporaryDirectory(prefix="mcpcat-manager-lifecycle-") as directory:
        os.chdir(directory)
        try:
            await check_lifecycle(directory)
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    asyncio.run(main())
