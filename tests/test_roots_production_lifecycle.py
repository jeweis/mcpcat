"""生产装配下旧 STDIO Roots 通知、数据库撤权和服务代际验证。"""

import asyncio
from pathlib import Path
import sys

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from test_gateway_client_capabilities import live_http
from test_skill_content_workflow import READ, content_app
from test_stdio_tasks_gateway import process_exited

from app.application import create_app
from app.services.config_service import ConfigService
from app.services.inspector_service import inspector_service
from app.services.market_service import MarketService
from app.services.roots_notifications import RootsNotifications
from app.services.server_manager import MCPServerManager
from app.storage.unit_of_work import UnitOfWork


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["finish", "revoke", "stop", "restart"])
async def test_stdio_roots_production_lifecycle(content_app, monkeypatch, ending):
    _, database = content_app
    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    assert ConfigService.add_server_to_config(
        "roots-source",
        {
            "type": "stdio",
            "command": sys.executable,
            "args": [str(Path(__file__).parent / "fixtures" / "mcp_stdio_roots.py")],
            "require_auth": True,
            "timeout": 15,
        },
    )
    manager = MCPServerManager()
    app = create_app(database=database, server_manager=manager)
    async with asyncio.timeout(60), live_http(app) as endpoint:
        server = manager.server_info["roots-source"]["mcp"]
        relay = next(
            item for item in server.middleware if isinstance(item, RootsNotifications)
        )
        roots, ready, pids = ["file:///fixture/before"], asyncio.Event(), []

        async def progress(value, total, message):
            pids.append(int(message))
            ready.set()

        url = endpoint + "/roots-source/"
        transport = StreamableHttpTransport(url, headers={"Mcpcat-Key": READ})
        async with Client(
            transport, mode="legacy", roots=lambda ctx: roots, progress_handler=progress
        ) as client:
            task = asyncio.create_task(client.call_tool("watch"))
            try:
                await asyncio.wait_for(ready.wait(), 15)
                roots[:] = ["file:///fixture/after"]
                if ending == "finish":
                    await client.session.send_roots_list_changed()
                    result = await task
                    assert result.data == {
                        "pid": pids[0],
                        "before": ["file:///fixture/before"],
                        "after": ["file:///fixture/after"],
                    }
                else:
                    if ending == "revoke":
                        with UnitOfWork(database) as unit:
                            unit.api_keys.update(READ, {"enabled": False})
                            unit.commit()
                    elif ending == "stop":
                        assert await manager.stop_server("roots-source")
                    else:
                        assert await manager.restart_server("roots-source")
                        replacement = manager.server_info["roots-source"]["mcp"]
                        assert replacement is not server
                        assert all(item is not relay for item in replacement.middleware)
                    # 使用真实 HTTP 入口提交原会话的通知，不绕过生产鉴权。
                    async with httpx2.AsyncClient() as probe:
                        response = await probe.post(
                            url,
                            headers={
                                "Mcpcat-Key": READ,
                                "Mcp-Session-Id": transport.get_session_id(),
                                "Mcp-Protocol-Version": "2025-11-25",
                                "Accept": "application/json, text/event-stream",
                            },
                            json={
                                "jsonrpc": "2.0",
                                "method": "notifications/roots/list_changed",
                            },
                        )
                    assert (
                        response.status_code
                        == {
                            "revoke": 401,
                            "stop": 503,
                            "restart": 404,
                        }[ending]
                    )
                    await asyncio.sleep(0.05)
                    if (
                        task.done()
                        and not task.cancelled()
                        and task.exception() is None
                    ):
                        pytest.fail("失效的旧调用不应因通知而返回成功")
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await process_exited(pids[0])
        async with asyncio.timeout(3):
            while relay.entries:
                await asyncio.sleep(0.01)
        if ending == "restart":
            ready.clear()
            roots[:] = ["file:///new/before"]
            async with Client(
                StreamableHttpTransport(url, headers={"Mcpcat-Key": READ}),
                mode="legacy",
                roots=lambda ctx: roots,
                progress_handler=progress,
            ) as fresh:
                task = asyncio.create_task(fresh.call_tool("watch"))
                try:
                    await asyncio.wait_for(ready.wait(), 15)
                    roots[:] = ["file:///new/after"]
                    await fresh.session.send_roots_list_changed()
                    result = await task
                    assert result.data["before"] == ["file:///new/before"]
                    assert result.data["after"] == ["file:///new/after"]
                    assert result.data["pid"] != pids[0]
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            await process_exited(pids[-1])
