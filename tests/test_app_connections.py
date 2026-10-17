"""真实管理请求的接入状态不泄漏草稿、秘密或假报已挂载。"""

import httpx2
import pytest
from sqlalchemy import select
from test_apps_mcp_gateway import bound_source, gateway, uploaded
from test_skill_content_workflow import READ, WRITE, content_app
from test_skills_mcp_gateway import rpc

from app.services.config_service import ConfigService
from app.storage.app_models import AppVersionRecord
from app.storage.unit_of_work import UnitOfWork


@pytest.mark.asyncio
async def test_ready_connection_matches_real_mcp_and_rejects_withdrawal(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            await uploaded(client)
            response = await client.get(
                "/api/apps/demo/connections", headers={"Mcpcat-Key": READ}
            )
            assert response.status_code == 200, response.text
            info = response.json()["connections"][0]
            assert info["ready"] is True
            assert info["version"] == "1.0.0"
            assert info["endpoint_url"] == "http://fixture/mcp/apps"
            assert info["require_auth"] is True
            tools = (await rpc(client, "tools/list", path="apps")).json()["result"][
                "tools"
            ]
            tool = next(item for item in tools if item["name"] == info["open_tool"])
            assert tool["_meta"]["ui"]["resourceUri"] == info["resource_uri"]
            assert READ not in response.text and WRITE not in response.text
            assert response.headers["cache-control"] == "private, no-store"
            await client.post(
                "/api/apps/demo/versions/1.0.0/withdraw",
                headers={"Mcpcat-Key": WRITE},
            )
            assert (
                await client.get(
                    "/api/apps/demo/connections", headers={"Mcpcat-Key": READ}
                )
            ).status_code == 404
            withdrawn = (
                await client.get(
                    "/api/apps/demo/connections", headers={"Mcpcat-Key": WRITE}
                )
            ).json()["connections"][0]
            assert withdrawn["ready"] is False
            assert withdrawn["resource_uri"] is None
            assert withdrawn["open_tool"] is None


@pytest.mark.asyncio
async def test_connections_close_on_source_stop_and_unsafe_base(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path, require_auth=False)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            await bound_source(app, client, lambda value: value, app_public=True)
            path = "/api/apps/bound/connections"
            headers = {"Mcpcat-Key": READ}
            assert (await client.get(path, headers=headers)).json()["connections"][0][
                "require_auth"
            ]
            assert (await client.get(path, headers=headers)).json()["connections"][0][
                "ready"
            ]
            app.state.server_manager.server_info["math"]["status"] = "stopped"
            stopped = (await client.get(path, headers=headers)).json()["connections"][0]
            assert not stopped["ready"] and stopped["endpoint_url"] is None
            app.state.server_manager.server_info["math"]["status"] = "running"
            monkeypatch.setattr(
                ConfigService,
                "get_public_base_url",
                lambda: "https://user:fixture-secret@example.test/?token=fixture-secret",
            )
            response = await client.get(path, headers=headers)
            assert not response.json()["connections"][0]["ready"]
            assert "fixture-secret" not in response.text


@pytest.mark.asyncio
async def test_connections_recheck_render_hash_and_runtime_alive(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            await uploaded(client)
            with UnitOfWork(database) as unit:
                row = unit.session.scalar(select(AppVersionRecord))
                row.rendered_sha256 = "0" * 64
                unit.commit()
            response = await client.get(
                "/api/apps/demo/connections", headers={"Mcpcat-Key": READ}
            )
            info = response.json()["connections"][0]
            assert not info["ready"] and info["resource_uri"] is None
            app.state.apps_mcp_runtime.alive = False
            response = await client.get(
                "/api/apps/demo/connections", headers={"Mcpcat-Key": READ}
            )
            assert not response.json()["connections"][0]["ready"]
