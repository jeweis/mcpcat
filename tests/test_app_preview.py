"""预览是有鉴权的静态装配，不执行用户内容或真实工具。"""

import httpx2
import pytest
from sqlalchemy import select
from test_app_management_api import apps_client, upload
from test_apps_mcp_gateway import bound_source, gateway
from test_skill_content_workflow import READ, WRITE, content_app

from app.services import app_preview
from app.storage.app_models import AppArtifactRecord, AppVersionRecord
from app.storage.unit_of_work import UnitOfWork


@pytest.mark.asyncio
async def test_preview_draft_write_only_json_and_raw_download_unchanged(apps_client):
    client, _ = apps_client
    content = b'<html><script>document.body.dataset.executed="yes";</script></html>'
    async with client:
        assert (await upload(client, content=content)).status_code == 200
        prefix = "/api/apps/demo/versions/1.0.0"
        assert (
            await client.get(prefix + "/preview", headers={"Mcpcat-Key": READ})
        ).status_code == 404
        response = await client.get(prefix + "/preview", headers={"Mcpcat-Key": WRITE})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["mode"] == "mock" and result["version"] == "1.0.0"
        assert 'document.body.dataset.executed="yes"' in result["html"]
        assert result["allowed_tools"] == []
        assert response.headers["content-type"].startswith("application/json")
        assert response.headers["cache-control"] == "private, no-store"
        assert READ not in response.text and WRITE not in response.text
        original = await client.get(prefix + "/download", headers={"Mcpcat-Key": WRITE})
        assert original.content == content
        assert (
            await client.get(prefix + "/preview", headers={"Mcpcat-Key": READ})
        ).status_code == 404


@pytest.mark.asyncio
async def test_preview_bound_tool_is_never_called_and_stopped_source_denied(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path)
    calls = []

    def value(value: int) -> int:
        calls.append(value)
        return value

    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            name = await bound_source(app, client, value)
            response = await client.get(
                "/api/apps/bound/versions/1.0.0/preview", headers={"Mcpcat-Key": READ}
            )
            assert response.status_code == 200, response.text
            assert response.json()["allowed_tools"] == [name]
            button = response.json()["initial_result"]["view"]["children"][0]
            assert button["onClick"]["tool"] == name
            assert calls == []
            app.state.server_manager.server_info["math"]["status"] = "stopped"
            response = await client.get(
                "/api/apps/bound/versions/1.0.0/preview", headers={"Mcpcat-Key": READ}
            )
            assert response.status_code == 409
            assert calls == []


@pytest.mark.asyncio
async def test_preview_refuses_corrupt_artifact(apps_client):
    client, database = apps_client
    async with client:
        assert (await upload(client)).status_code == 200
        with UnitOfWork(database) as unit:
            artifact = unit.session.scalar(select(AppArtifactRecord))
            artifact.sha256 = "0" * 64
            unit.commit()
        response = await client.get(
            "/api/apps/demo/versions/1.0.0/preview", headers={"Mcpcat-Key": WRITE}
        )
        assert response.status_code == 409
        assert "html" not in response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["config", "revoke"])
async def test_preview_rechecks_config_and_key_after_render(
    apps_client, monkeypatch, change
):
    client, database = apps_client
    original = app_preview.render_app

    def changing(info, content, app_id):
        result = original(info, content, app_id)
        with UnitOfWork(database) as unit:
            if change == "config":
                row = unit.session.scalar(select(AppVersionRecord))
                row.changelog = "Changed during render"
            else:
                unit.api_keys.update(WRITE, {"enabled": False})
            unit.commit()
        return result

    async with client:
        assert (await upload(client)).status_code == 200
        monkeypatch.setattr(app_preview, "render_app", changing)
        response = await client.get(
            "/api/apps/demo/versions/1.0.0/preview", headers={"Mcpcat-Key": WRITE}
        )
        assert response.status_code == (409 if change == "config" else 403)
        assert "html" not in response.json()
