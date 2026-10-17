"""生产应用工厂/管理器的 Tasks 路由、撤权与服务代际。"""

import asyncio

import httpx2
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp_tasks import TasksExtension, call_tool_task
from mcp.shared.exceptions import MCPError
from test_gateway_client_capabilities import live_http
from test_skill_content_workflow import READ, content_app

from app.application import create_app
from app.services.config_service import ConfigService
from app.services.inspector_service import inspector_service
from app.services.market_service import MarketService
from app.services.server_manager import MCPServerManager
from app.storage.unit_of_work import UnitOfWork


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["restart", "revoke", "stop"])
async def test_tasks_production_lifecycle(content_app, monkeypatch, ending):
    _, database = content_app
    source = FastMCP("task fixture")
    source.add_extension(TasksExtension(url="memory://", name="gateway-task-fixture"))
    calls = []

    @source.tool(task=True)
    async def compute(value: int) -> int:
        calls.append(value)
        return value + 1

    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    async with asyncio.timeout(50), live_http(source.http_app(path="/mcp")) as upstream:
        assert ConfigService.add_server_to_config(
            "task-source",
            {
                "type": "streamable-http",
                "url": upstream,
                "require_auth": True,
            },
        )
        manager = MCPServerManager()
        app = create_app(database=database, server_manager=manager)
        async with live_http(app) as endpoint:
            transport = StreamableHttpTransport(
                endpoint + "/task-source/", headers={"Mcpcat-Key": READ}
            )
            async with Client(transport) as client:
                handle = await call_tool_task(client, "compute", {"value": 1})
                assert "2" in str(await handle.result())
                assert calls == [1]
                if ending == "restart":
                    assert await manager.restart_server("task-source")
                    with pytest.raises(MCPError, match="Task unavailable"):
                        await handle.status()
                    next_handle = await call_tool_task(client, "compute", {"value": 2})
                    assert next_handle.task_id != handle.task_id
                    assert "3" in str(await next_handle.result())
                    assert calls == [1, 2]
                elif ending == "revoke":
                    with UnitOfWork(database) as unit:
                        unit.api_keys.update(READ, {"enabled": False})
                        unit.commit()
                else:
                    assert await manager.stop_server("task-source")
                if ending != "restart":
                    async with httpx2.AsyncClient() as probe:
                        for method in ("tasks/get", "tasks/cancel", "tasks/update"):
                            response = await probe.post(
                                endpoint + "/task-source/",
                                headers={"Mcpcat-Key": READ},
                                json={
                                    "jsonrpc": "2.0",
                                    "id": 1,
                                    "method": method,
                                    "params": {"taskId": handle.task_id},
                                },
                            )
                            assert response.status_code == (
                                401 if ending == "revoke" else 503
                            )
                    assert calls == [1]
        assert not manager.lifecycle.background
