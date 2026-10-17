"""写入已开始时撤权：不交付成功结果，也不重试或虚构副作用回滚。"""

import asyncio

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from test_apps_mcp_gateway import bound_source
from test_catalog_uploaded_apps import catalog_gateway
from test_skill_content_workflow import READ, WRITE
from test_skill_content_workflow import content_app as _content_app

from app.storage.unit_of_work import UnitOfWork

content_app = _content_app


async def revoke(app, management, database, change):
    if change == "key":
        with UnitOfWork(database) as unit:
            unit.api_keys.update(READ, {"enabled": False})
            unit.commit()
    elif change == "withdraw":
        response = await management.post(
            "/api/apps/bound/versions/1.0.0/withdraw", headers={"Mcpcat-Key": WRITE}
        )
        assert response.status_code == 200, response.text
    else:
        app.state.server_manager.server_info["math"]["status"] = "stopped"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("path", ["apps", "mcpcat"])
@pytest.mark.parametrize("change", ["key", "withdraw", "source-stop"])
async def test_inflight_write_after_revocation_never_reports_success(
    content_app, monkeypatch, tmp_path, mode, path, change
):
    _, database = content_app
    entered, release = asyncio.Event(), asyncio.Event()
    effects = []

    async def value(value: int) -> int:
        effects.append(value)
        entered.set()
        await release.wait()
        return value

    async with catalog_gateway(database, monkeypatch, tmp_path) as (app, management):
        callback = await bound_source(app, management, value)
        transport = StreamableHttpTransport(
            f"http://fixture/mcp/{path}/",
            headers={"Mcpcat-Key": READ},
            httpx_client_factory=lambda **options: httpx2.AsyncClient(
                **options, transport=httpx2.ASGITransport(app=app)
            ),
        )
        async with Client(transport, mode=mode) as client:
            pending = asyncio.create_task(
                client.call_tool(callback, {"value": 7}, raise_on_error=False)
            )
            try:
                await asyncio.wait_for(entered.wait(), 5)
                await revoke(app, management, database, change)
            finally:
                release.set()
            result = await asyncio.wait_for(pending, 5)
            assert result.is_error and "部分操作可能已完成" in str(result)
            assert effects == [7]
