"""先验证固定 SDK 的真实 STDIO Tasks 协商，不猜测底层支持。"""

import asyncio
import os
import sys
from pathlib import Path
from time import monotonic

import pytest
from fastmcp_tasks import call_tool_task
from fastmcp_tasks.client import TasksClientExtension
from mcp.shared.exceptions import MCPError

from app.services.proxy_clients import make_proxy_client_factory
from app.services.proxy_task_connection import TaskConnection


@pytest.mark.asyncio
async def test_stdio_modern_tasks_creation_and_result():
    factory = make_proxy_client_factory(
        {
            "type": "stdio",
            "command": sys.executable,
            "args": [str(Path(__file__).parent / "fixtures" / "mcp_stdio_tasks.py")],
            "env": {},
            "timeout": 10,
        }
    )
    client = factory(extensions=[TasksClientExtension()])
    try:
        async with asyncio.timeout(30), client:
            assert client.protocol_version == "2026-07-28"
            handle = await call_tool_task(client, "identify", {"delay": 0.1})
            assert (await handle.status()).task_id == handle.task_id
            result = await handle.result()
            assert result.structured_content["pid"] > 0
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["close", "expire", "revoke"])
async def test_stdio_task_connection_keeps_process_and_releases(ending):
    factory = make_proxy_client_factory(
        {
            "type": "stdio",
            "command": sys.executable,
            "args": [str(Path(__file__).parent / "fixtures" / "mcp_stdio_tasks.py")],
            "env": {},
            "timeout": 10,
        }
    )
    allowed = True
    connection = TaskConnection(factory, lambda: allowed)
    async with asyncio.timeout(30):
        await connection.open()
        try:
            async with connection.use() as client:
                handle = await call_tool_task(client, "identify", {"delay": 0.1})
            # 请求已结束，但任务创建的进程必须仍可用，不能创建新客户端查询。
            await asyncio.sleep(0.1)
            async with connection.use() as same_client:
                assert same_client is client
                assert (await handle.status()).task_id == handle.task_id
                pid = (await handle.result()).structured_content["pid"]
            os.kill(pid, 0)
            if ending == "close":
                await connection.close()
            elif ending == "expire":
                connection.expires = monotonic()
            else:
                allowed = False
            await asyncio.wait_for(asyncio.shield(connection.task), 5)
            with pytest.raises(MCPError, match="Task connection unavailable"):
                async with connection.use():
                    pytest.fail("已结束连接不能恢复")
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            await connection.close()
