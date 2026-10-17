"""Apps 明确来源/别名、发布快照与实时调用授权测试。"""

import httpx2
import pytest
from fastmcp import FastMCP
from fastmcp.tools import Tool
from test_app_management_api import upload
from test_catalog import server_info
from test_skill_content_workflow import READ, WRITE, content_app

from app.core.config import settings
from app.services import app_tool_bindings
from app.services.app_content_service import AppDomainError
from app.services.app_tool_bindings import AppToolResolver, call_app_tool
from app.services.config_service import ConfigService
from app.services.security_service import security_service
from app.storage.runtime_identities import endpoint_identity
from app.storage.unit_of_work import UnitOfWork


@pytest.fixture
def bound_app(content_app, monkeypatch, tmp_path):
    app, database = content_app
    monkeypatch.setattr(settings, "mcpcat_apps_artifacts_path", str(tmp_path / "apps"))
    manager = app.state.server_manager
    manager.server_info = {}
    for name in ["a", "b"]:
        ConfigService.add_server_to_config(
            name, {"type": "stdio", "command": "fixture-never-run"}
        )
        server = FastMCP(name)

        def lookup(value: int, _name=name) -> dict:
            return {"source": _name, "value": value}

        server.add_tool(Tool.from_function(lookup, name="lookup"))
        manager.server_info[name] = server_info(server)
    resolver = AppToolResolver(
        database, manager, lambda: security_service.verify_api_key(READ) is not None
    )
    return app, database, resolver


async def prepare(client):
    assert (await upload(client)).status_code == 200
    result = await client.put(
        "/api/apps/demo/versions/1.0.0/bindings",
        headers={"Mcpcat-Key": WRITE},
        json={
            "bindings": [
                {"alias": "first", "server_name": "a", "tool_name": "lookup"},
                {"alias": "second", "server_name": "b", "tool_name": "lookup"},
            ]
        },
    )
    assert result.status_code == 200, result.text
    return result


async def publish(client):
    return await client.post(
        "/api/apps/demo/versions/1.0.0/publish",
        headers={"Mcpcat-Key": WRITE},
        json={"reviewed": True},
    )


@pytest.mark.asyncio
async def test_cross_source_aliases_freeze_and_dispatch(bound_app):
    app, database, resolver = bound_app
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        result = await prepare(client)
        assert len(result.json()["versions"][0]["bindings"]) == 2
        assert (await publish(client)).status_code == 200
        for alias, source in [("first", "a"), ("second", "b")]:
            result = await call_app_tool(
                database,
                resolver,
                slug="demo",
                version="1.0.0",
                alias=alias,
                arguments={"value": 7},
            )
            assert result.structured_content == {"source": source, "value": 7}
        result = await client.put(
            "/api/apps/demo/versions/1.0.0/bindings",
            headers={"Mcpcat-Key": WRITE},
            json={"bindings": []},
        )
        assert result.status_code == 409
        with pytest.raises(AppDomainError, match="未被"):
            await call_app_tool(
                database,
                resolver,
                slug="demo",
                version="1.0.0",
                alias="a__lookup",
                arguments={"value": 7},
            )


@pytest.mark.asyncio
async def test_failed_binding_review_preserves_previous_snapshot(bound_app):
    app, _, _ = bound_app
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        await prepare(client)
        for bindings in [
            [{"alias": "missing", "server_name": "a", "tool_name": "unknown"}],
            [
                {"alias": "same", "server_name": name, "tool_name": "lookup"}
                for name in ["a", "b"]
            ],
            [{"alias": "other", "server_name": "unknown", "tool_name": "lookup"}],
        ]:
            response = await client.put(
                "/api/apps/demo/versions/1.0.0/bindings",
                headers={"Mcpcat-Key": WRITE},
                json={"bindings": bindings},
            )
            assert response.status_code in {400, 409}, response.text
        detail = (
            await client.get("/api/apps/demo", headers={"Mcpcat-Key": WRITE})
        ).json()
        assert {row["alias"] for row in detail["versions"][0]["bindings"]} == {
            "first",
            "second",
        }


@pytest.mark.asyncio
async def test_schema_change_and_recreated_identity_require_new_review(bound_app):
    app, _, _ = bound_app
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        await prepare(client)
        ConfigService.remove_server_from_config("a")
        ConfigService.add_server_to_config(
            "a", {"type": "stdio", "command": "fixture-never-run"}
        )
        endpoint_identity(app.state.database, "service", "a")
        assert (await publish(client)).status_code == 409
        await client.put(
            "/api/apps/demo/versions/1.0.0/bindings",
            headers={"Mcpcat-Key": WRITE},
            json={"bindings": []},
        )
        assert (await publish(client)).status_code == 200


@pytest.mark.asyncio
async def test_stop_key_revoke_and_withdraw_block_live_calls(bound_app):
    app, database, resolver = bound_app
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        await prepare(client)
        assert (await publish(client)).status_code == 200
        app.state.server_manager.server_info["a"]["status"] = "stopped"
        with pytest.raises(AppDomainError, match="不可用"):
            await call_app_tool(
                database,
                resolver,
                slug="demo",
                version="1.0.0",
                alias="first",
                arguments={"value": 1},
            )
        app.state.server_manager.server_info["a"]["status"] = "running"
        with UnitOfWork(database) as unit:
            unit.api_keys.update(READ, {"enabled": False})
            unit.commit()
        with pytest.raises(AppDomainError, match="无权"):
            await call_app_tool(
                database,
                resolver,
                slug="demo",
                version="1.0.0",
                alias="first",
                arguments={"value": 1},
            )
        with UnitOfWork(database) as unit:
            unit.api_keys.update(READ, {"enabled": True})
            unit.commit()
        await client.post(
            "/api/apps/demo/versions/1.0.0/withdraw", headers={"Mcpcat-Key": WRITE}
        )
        with pytest.raises(AppDomainError, match="不存在"):
            await call_app_tool(
                database,
                resolver,
                slug="demo",
                version="1.0.0",
                alias="first",
                arguments={"value": 1},
            )


@pytest.mark.asyncio
async def test_revocation_inside_tool_reports_partial_completion_without_retry(
    bound_app, caplog
):
    app, database, resolver = bound_app
    invoked = []
    server = app.state.server_manager.server_info["a"]["mcp"]

    def revoke(value: int) -> int:
        invoked.append(value)
        with UnitOfWork(database) as unit:
            unit.api_keys.update(READ, {"enabled": False})
            unit.commit()
        return value

    server.add_tool(Tool.from_function(revoke, name="lookup"))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        await prepare(client)
        assert (await publish(client)).status_code == 200
        with caplog.at_level("INFO", logger="app.services.app_tool_bindings"):
            with pytest.raises(AppDomainError, match="部分操作可能已完成"):
                await call_app_tool(
                    database,
                    resolver,
                    slug="demo",
                    version="1.0.0",
                    alias="first",
                    arguments={"value": 7},
                )
        assert invoked == [7]
        assert READ not in caplog.text and WRITE not in caplog.text
        assert "'outcome': 'authorization_changed_after_execution'" in caplog.text


@pytest.mark.asyncio
async def test_changed_schema_blocks_binding_publication(bound_app):
    app, _, _ = bound_app
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        await prepare(client)
        server = app.state.server_manager.server_info["a"]["mcp"]

        def changed(value: int, required: str) -> str:
            return required

        server.add_tool(Tool.from_function(changed, name="lookup"))
        response = await publish(client)
        assert response.status_code == 409, response.text
        assert "重新审核" in response.json()["detail"]


@pytest.mark.asyncio
async def test_write_key_revoked_before_empty_binding_commit_is_denied(
    bound_app, monkeypatch
):
    app, database, _ = bound_app
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        await prepare(client)
        original = app_tool_bindings.run_in_threadpool

        async def revoke_before_save(callback, *args, **kwargs):
            with UnitOfWork(database) as unit:
                unit.api_keys.update(WRITE, {"permission": "read"})
                unit.commit()
            return await original(callback, *args, **kwargs)

        monkeypatch.setattr(app_tool_bindings, "run_in_threadpool", revoke_before_save)
        response = await client.put(
            "/api/apps/demo/versions/1.0.0/bindings",
            headers={"Mcpcat-Key": WRITE},
            json={"bindings": []},
        )
        assert response.status_code == 403, response.text
        rows = app_tool_bindings.app_binding_rows(
            database, "demo", "1.0.0", include_drafts=True
        )
        assert {row["alias"] for row in rows} == {"first", "second"}
