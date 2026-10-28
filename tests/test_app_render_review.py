"""发布预检、冻结派生摘要和旧数据库增量升级。"""

import io
import zipfile

import pytest
from sqlalchemy import inspect, select, text
from test_app_management_api import apps_client, upload
from test_skill_content_workflow import READ, WRITE, content_app

from alembic import command
from app.services.app_content_service import AppDomainError, load_app_content
from app.services.app_rendering import render_app
from app.storage.app_models import AppVersionRecord
from app.storage.database import Database
from app.storage.migrations import create_alembic_config, upgrade_schema
from app.storage.unit_of_work import UnitOfWork


@pytest.mark.asyncio
async def test_publication_records_html_digest_and_drift_denies_read(apps_client):
    client, database = apps_client
    async with client:
        assert (await upload(client)).status_code == 200
        response = await client.post(
            "/api/apps/demo/versions/1.0.0/publish",
            headers={"Mcpcat-Key": WRITE},
            json={"reviewed": True},
        )
        assert response.status_code == 200, response.text
        version = response.json()["versions"][0]
        assert len(version["rendered_sha256"]) == 64
        assert len(version["rendered_payload_sha256"]) == 64
        assert version["rendering_version"] == "html-assembly-5"
        with UnitOfWork(database) as unit:
            row = unit.session.scalar(select(AppVersionRecord))
            app_id = row.app_id
        info, content = load_app_content(database, slug="demo", version="1.0.0")
        assert render_app(info, content, app_id).sha256 == version["rendered_sha256"]
        changed = {**info, "rendered_payload_sha256": "0" * 64}
        with pytest.raises(AppDomainError, match="派生数据"):
            render_app(changed, content, app_id)
        info["rendered_sha256"] = "0" * 64
        with pytest.raises(AppDomainError, match="审核不一致"):
            render_app(info, content, app_id)
        # 原始下载不被派生摘要校验改写。
        assert (
            await client.get(
                "/api/apps/demo/versions/1.0.0/download", headers={"Mcpcat-Key": READ}
            )
        ).content == content


@pytest.mark.asyncio
async def test_unsupported_assembly_cannot_be_published_and_remains_draft(apps_client):
    client, _ = apps_client
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("index.html", '<script src="app.js" type="module"></script>')
        archive.writestr("app.js", 'import "./missing.js";')
    async with client:
        assert (
            await upload(client, content=output.getvalue(), filename="app.zip")
        ).status_code == 200
        response = await client.post(
            "/api/apps/demo/versions/1.0.0/publish",
            headers={"Mcpcat-Key": WRITE},
            json={"reviewed": True},
        )
        assert response.status_code == 409
        detail = (
            await client.get("/api/apps/demo", headers={"Mcpcat-Key": WRITE})
        ).json()
        assert detail["versions"][0]["status"] == "draft"
        assert detail["versions"][0]["rendered_sha256"] is None
        assert (
            await client.get("/api/apps", headers={"Mcpcat-Key": READ})
        ).json() == []


@pytest.mark.asyncio
async def test_module_package_publication_freezes_derived_not_original_bytes(
    apps_client,
):
    client, database = apps_client
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "index.html",
            '<script type="importmap">{"imports":{"alias":"./js/dep.js"}}</script><link rel="modulepreload" href="js/main.js?v=1"><script type="module" src="js/main.js?v=1"></script>',
        )
        archive.writestr(
            "js/main.js",
            'const name="alias"; const loaded=await import(name); document.body.textContent=String(loaded.value);',
        )
        archive.writestr("js/dep.js", "export const value=7;")
    raw = output.getvalue()
    async with client:
        assert (
            await upload(client, content=raw, filename="app.zip")
        ).status_code == 200
        response = await client.post(
            "/api/apps/demo/versions/1.0.0/publish",
            headers={"Mcpcat-Key": WRITE},
            json={"reviewed": True},
        )
        assert response.status_code == 200, response.text
        version = response.json()["versions"][0]
        assert version["rendering_version"] == "html-assembly-5"
        preview = await client.get(
            "/api/apps/demo/versions/1.0.0/preview", headers={"Mcpcat-Key": READ}
        )
        assert preview.status_code == 200, preview.text
        assert 'type="importmap"' in preview.json()["html"]
        assert "data-mcpcat-module-config" in preview.json()["html"]
        assert preview.json()["requested_metadata"]["ui"]["csp"]["resourceDomains"] == [
            "data:"
        ]
        assert (
            await client.get(
                "/api/apps/demo/versions/1.0.0/download", headers={"Mcpcat-Key": READ}
            )
        ).content == raw
    info, content = load_app_content(database, slug="demo", version="1.0.0")
    with UnitOfWork(database) as unit:
        app_id = unit.apps.get("demo").id
    assert render_app(info, content, app_id).sha256 == version["rendered_sha256"]


@pytest.mark.asyncio
async def test_v4_published_modules_remain_on_frozen_renderer_after_v5(apps_client):
    client, database = apps_client
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("index.html", '<script type="module" src="app.js"></script>')
        archive.writestr(
            "app.js", 'import "./dep.js"; export const url=import.meta.url;'
        )
        archive.writestr("dep.js", "window.oldModule=true;")
    raw = output.getvalue()
    async with client:
        assert (
            await upload(client, content=raw, filename="app.zip")
        ).status_code == 200
    info, content = load_app_content(
        database, slug="demo", version="1.0.0", include_drafts=True
    )
    with UnitOfWork(database) as unit:
        row = unit.session.scalar(select(AppVersionRecord))
        old = render_app(
            {**info, "rendering_version": "html-assembly-4"}, content, row.app_id
        )
        assert "data-mcpcat-module-config" not in old.html
        row.status = "published"
        unit.apps.get("demo").status = "published"
        row.rendering_version, row.rendered_sha256 = old.renderer_version, old.sha256
        row.rendered_payload_sha256 = old.payload_sha256()
        app_id = row.app_id
        unit.commit()
    current, content = load_app_content(database, slug="demo", version="1.0.0")
    assert current["rendering_version"] == "html-assembly-4"
    assert render_app(current, content, app_id) == old and content == raw


def test_incremental_render_review_migration_preserves_existing_app_rows(
    tmp_path, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "mcpcat_backups_path", str(tmp_path / "backups"))
    database = Database(tmp_path / "old-apps.db")
    command.upgrade(create_alembic_config(database.path), "0004_skill_content_variants")
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO apps(id,slug,display_name,description,source_type,source_ref_json,status,visibility,created_at,updated_at) VALUES (1,'old','Old','unchanged','uploaded','{}','published','private',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO app_versions(id,app_id,version,status,content_format,entrypoint,manifest_json,csp_json,permissions_json,changelog,created_at) VALUES (1,1,'1.0.0','published','html','index.html','{}','{}','{}','untouched',CURRENT_TIMESTAMP)"
            )
        )
    assert upgrade_schema(database).current == "0006_wecom_login"
    with database.engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT version,status,changelog,rendered_sha256,rendering_version FROM app_versions WHERE id=1"
            )
        ).one()
        assert tuple(row) == ("1.0.0", "published", "untouched", None, None)
    assert {"skills", "app_versions", "skill_content_variants"}.issubset(
        inspect(database.engine).get_table_names()
    )
    database.dispose()
