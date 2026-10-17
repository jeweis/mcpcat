"""只在隔离快照中运行旧程序的恢复验收子进程。"""

import asyncio
import importlib.metadata
import json
import socket
import sys
from pathlib import Path
from unittest.mock import patch


async def check_mcp(root: str) -> None:
    """旧 SDK 经恢复后的旧 HTTP/SSE 代理调用可信回环上游。"""
    from fastmcp import Client
    from fastmcp.client.transports import SSETransport, StreamableHttpTransport

    for transport, path in (
        (StreamableHttpTransport, "/mcp/recovery-source"),
        (SSETransport, "/sse/recovery-source/"),
    ):
        async with Client(
            transport(
                root + path, headers={"Mcpcat-Key": "published-cli-private-fixture-key"}
            )
        ) as client:
            result = await client.call_tool("echo", {"value": "recovered"})
            assert result.data == "recovered"


async def serve(source: Path, data: Path) -> None:
    """数据、配置和网络仅来自父探针的一次性目录及回环夹具。"""
    sys.path.insert(0, str(source))
    import uvicorn

    import app.application
    from app.core.config import settings
    from app.services.inspector_service import InspectorService
    from app.services.market_service import MarketService
    from app.storage.database import Database

    assert Path(app.application.__file__).resolve().is_relative_to(source)
    assert importlib.metadata.version("fastmcp") == "3.4.4"
    settings.mcpcat_config_path = str(data / "absent.json")
    settings.mcpcat_artifacts_path = str(data / "artifacts")
    settings.mcpcat_backups_path = str(data / "backups")
    database = Database(data / "fixture.db")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    root = f"http://127.0.0.1:{listener.getsockname()[1]}"
    with patch.object(MarketService, "refresh_async", lambda self: None), patch.object(
        InspectorService, "start_cleanup_task", lambda self: None
    ):
        application = app.application.create_app(database=database)
        runner = uvicorn.Server(uvicorn.Config(application, log_level="error"))
        task = asyncio.create_task(runner.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not runner.started:
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
                await check_mcp(root)
            announce_ready(root)
            await task
        finally:
            runner.should_exit = True
            await task
            listener.close()
            database.dispose()


def announce_ready(root: str) -> None:
    """真实双传输调用成功后报告，不把启动进程当验收通过。"""
    report = {
        "url": root,
        "fastmcp": "3.4.4",
        "mcp": importlib.metadata.version("mcp"),
        "http_sse": "passed",
    }
    print("MCPCAT_RECOVERY_READY " + json.dumps(report), flush=True)


if __name__ == "__main__":
    asyncio.run(serve(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()))
