"""生产 STDIO Tasks 使用原进程，停服/重启/撤权实际释放任务进程。"""

import asyncio
import os
from pathlib import Path
import sys

import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from fastmcp_tasks import call_tool_task
from mcp.shared.exceptions import MCPError
from test_gateway_client_capabilities import live_http
from test_skill_content_workflow import READ, content_app

from app.application import create_app
from app.services.config_service import ConfigService
from app.services.inspector_service import inspector_service
from app.services.market_service import MarketService
from app.services.server_manager import MCPServerManager
from app.storage.unit_of_work import UnitOfWork


async def process_exited(pid):
    async with asyncio.timeout(8):
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            await asyncio.sleep(0.05)


def task_app(content_app, monkeypatch):
    """Build an isolated production application with the trusted STDIO fixture."""
    _, database = content_app
    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    assert ConfigService.add_server_to_config(
        "stdio-task",
        {
            "type": "stdio",
            "command": sys.executable,
            "args": [str(Path(__file__).parent / "fixtures" / "mcp_stdio_tasks.py")],
            "require_auth": True,
            "timeout": 10,
        },
    )
    manager = MCPServerManager()
    app = create_app(database=database, server_manager=manager)
    return app, manager, database


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["stop", "restart", "revoke"])
async def test_stdio_task_gateway_lifecycle(content_app, monkeypatch, ending):
    app, manager, database = task_app(content_app, monkeypatch)
    async with asyncio.timeout(60), live_http(app) as endpoint:
        transport = StreamableHttpTransport(
            endpoint + "/stdio-task/", headers={"Mcpcat-Key": READ}
        )
        async with Client(transport) as client:
            handle = await call_tool_task(client, "identify", {"delay": 0.1})
            result = await handle.result()
            pid = result.structured_content["pid"]
            # 原 HTTP 提交已返回，仍在同一 STDIO 任务中读取结果。
            assert (await handle.status()).result["structuredContent"]["pid"] == pid
            os.kill(pid, 0)
            if ending == "stop":
                assert await manager.stop_server("stdio-task")
            elif ending == "restart":
                assert await manager.restart_server("stdio-task")
                with pytest.raises(MCPError, match="Task unavailable"):
                    await handle.status()
                next_handle = await call_tool_task(client, "identify", {})
                next_pid = (await next_handle.result()).structured_content["pid"]
                assert next_pid != pid
            else:
                with UnitOfWork(database) as unit:
                    unit.api_keys.update(READ, {"enabled": False})
                    unit.commit()
            await process_exited(pid)
    if ending == "restart":
        await process_exited(next_pid)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["accept", "decline", "cancel", "cancel-task"])
async def test_stdio_task_input_in_original_worker(content_app, monkeypatch, action):
    app, manager, _ = task_app(content_app, monkeypatch)
    questions = []

    async def answer(message, response_type, params, context):
        questions.append(message)
        return ElicitResult(
            action=action, content={"value": "approved"} if action == "accept" else None
        )

    async with asyncio.timeout(60), live_http(app) as endpoint:
        transport = StreamableHttpTransport(
            endpoint + "/stdio-task/", headers={"Mcpcat-Key": READ}
        )
        async with Client(transport, elicitation_handler=answer) as client:
            handle = await call_tool_task(client, "confirm_identity", {})
            waiting = await handle.wait(state="input_required", timeout=10)
            assert len(waiting.input_requests) == 1
            question = next(iter(waiting.input_requests.values()))
            message = question["params"]["message"]
            pid = int(message.rsplit(" ", 1)[1])
            os.kill(pid, 0)
            if action == "cancel-task":
                await handle.cancel()
                assert (
                    await handle.wait(state="cancelled", timeout=5)
                ).status == "cancelled"
                assert questions == []
            else:
                result = (await handle.result()).structured_content
                assert result == {
                    "pid": pid,
                    "action": action,
                    "content": {"value": "approved"} if action == "accept" else None,
                }
                assert questions == [message]
                assert (await handle.status()).status == "completed"
            assert await manager.stop_server("stdio-task")
            await process_exited(pid)


@pytest.mark.asyncio
async def test_stdio_running_task_cancel_has_no_success_result(
    content_app, monkeypatch
):
    app, manager, _ = task_app(content_app, monkeypatch)
    async with asyncio.timeout(60), live_http(app) as endpoint:
        transport = StreamableHttpTransport(
            endpoint + "/stdio-task/", headers={"Mcpcat-Key": READ}
        )
        async with Client(transport) as client:
            handle = await call_tool_task(client, "identify", {"delay": 10})
            assert (await handle.wait(state="working", timeout=5)).status == "working"
            await handle.cancel()
            assert (
                await handle.wait(state="cancelled", timeout=5)
            ).status == "cancelled"
            with pytest.raises(ToolError, match="cancelled"):
                await handle.result()
            # 后续读取仍为取消终态，不恢复执行或伪装成成功结果。
            assert (await handle.status()).result is None
            assert (await handle.status()).status == "cancelled"
            assert await manager.stop_server("stdio-task")
