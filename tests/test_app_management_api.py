"""真实管理 HTTP 的 Apps 生命周期、权限和原始文件契约。"""

import hashlib
import io
import zipfile

import httpx2
import pytest
from sqlalchemy import select
from test_skill_content_workflow import READ, WRITE, content_app

from app.core.config import settings
from app.storage.app_models import AppArtifactRecord, AppVersionRecord
from app.storage.unit_of_work import UnitOfWork


@pytest.fixture
def apps_client(content_app, monkeypatch, tmp_path):
    app, database = content_app
    monkeypatch.setattr(settings, "mcpcat_apps_artifacts_path", str(tmp_path / "apps"))
    return (
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ),
        database,
    )


async def upload(
    client,
    *,
    version="1.0.0",
    content=b"<html>Original</html>\r\n",
    filename="index.html",
    key=WRITE,
):
    return await client.post(
        "/api/apps/upload",
        headers={"Mcpcat-Key": key},
        data={"slug": "demo", "display_name": "Demo", "version": version},
        files={"file": (filename, content)},
    )


@pytest.mark.asyncio
async def test_upload_review_publish_freeze_and_withdraw(apps_client):
    client, _ = apps_client
    async with client:
        assert (await upload(client, key=READ)).status_code == 403
        created = await upload(client)
        assert created.status_code == 200, created.text
        assert created.json()["versions"][0]["status"] == "draft"
        assert (
            await client.get("/api/apps", headers={"Mcpcat-Key": READ})
        ).json() == []
        assert (
            await client.get("/api/apps/demo", headers={"Mcpcat-Key": READ})
        ).status_code == 404
        prefix = "/api/apps/demo/versions/1.0.0"
        assert (
            await client.post(
                f"{prefix}/publish", headers={"Mcpcat-Key": WRITE}, json={}
            )
        ).status_code == 400
        config = {
            "changelog": "Reviewed",
            "csp": {"connectDomains": ["https://example.com"]},
            "permissions": {"clipboardWrite": {}},
        }
        assert (
            await client.put(
                f"{prefix}/config", headers={"Mcpcat-Key": WRITE}, json=config
            )
        ).status_code == 200
        published = await client.post(
            f"{prefix}/publish", headers={"Mcpcat-Key": WRITE}, json={"reviewed": True}
        )
        assert published.status_code == 200, published.text
        assert published.json()["visibility"] == "private"
        detail = (
            await client.get("/api/apps/demo", headers={"Mcpcat-Key": READ})
        ).json()
        assert detail["versions"][0]["csp"] == config["csp"]
        assert detail["versions"][0]["changelog"] == "Reviewed"
        assert (
            await client.put(
                f"{prefix}/config", headers={"Mcpcat-Key": WRITE}, json=config
            )
        ).status_code == 409
        assert (await upload(client)).status_code == 409
        assert (
            await client.post(
                f"{prefix}/publish",
                headers={"Mcpcat-Key": WRITE},
                json={"reviewed": True},
            )
        ).status_code == 409
        withdrawn = await client.post(
            f"{prefix}/withdraw", headers={"Mcpcat-Key": WRITE}
        )
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json()["versions"][0]["status"] == "deprecated"
        assert (
            await client.get(f"{prefix}/download", headers={"Mcpcat-Key": READ})
        ).status_code == 404
        assert (
            await client.get(f"{prefix}/download", headers={"Mcpcat-Key": WRITE})
        ).status_code == 200


@pytest.mark.asyncio
async def test_latest_does_not_leak_new_draft_and_old_download_unchanged(apps_client):
    client, _ = apps_client
    original = b"<html>Original</html>\r\n"
    async with client:
        await upload(client, content=original)
        await client.post(
            "/api/apps/demo/versions/1.0.0/publish",
            headers={"Mcpcat-Key": WRITE},
            json={"reviewed": True},
        )
        await upload(client, version="1.1.0", content=b"New draft")
        listing = (await client.get("/api/apps", headers={"Mcpcat-Key": READ})).json()
        assert listing[0]["latest_version"] == "1.0.0"
        listing = (await client.get("/api/apps", headers={"Mcpcat-Key": WRITE})).json()
        assert listing[0]["latest_version"] == "1.1.0"
        download = await client.get(
            "/api/apps/demo/versions/1.0.0/download", headers={"Mcpcat-Key": READ}
        )
        assert download.content == original
        assert (
            download.headers["X-Checksum-Sha256"]
            == hashlib.sha256(original).hexdigest()
        )
        assert download.headers["Content-Disposition"].startswith("attachment;")
        assert download.headers["Cache-Control"] == "private, no-store"
        assert download.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_file_preview_is_json_and_package_scripts_never_run(
    apps_client, tmp_path
):
    client, _ = apps_client
    marker = tmp_path / "never-executed"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("index.html", "<script>window.notParent = true</script>")
        archive.writestr("README.md", "# Instructions")
        archive.writestr("asset.bin", b"\xff\xfe")
        archive.writestr("large.txt", "x" * (512 * 1024 + 1))
        archive.writestr("script.py", f"open({str(marker)!r}, 'w').write('ran')")
    async with client:
        result = await upload(client, content=output.getvalue(), filename="app.zip")
        assert result.status_code == 200, result.text
        path = "/api/apps/demo/versions/1.0.0/file"
        html = await client.get(
            path, params={"path": "index.html"}, headers={"Mcpcat-Key": WRITE}
        )
        assert html.headers["Content-Type"].startswith("application/json")
        assert html.headers["Cache-Control"] == "private, no-store"
        assert html.json()["content"].startswith("<script>")
        markdown = (
            await client.get(
                path, params={"path": "README.md"}, headers={"Mcpcat-Key": WRITE}
            )
        ).json()
        assert markdown["is_markdown"] and markdown["previewable"]
        binary = (
            await client.get(
                path, params={"path": "asset.bin"}, headers={"Mcpcat-Key": WRITE}
            )
        ).json()
        assert binary["reason"] == "binary" and binary["content"] is None
        large = (
            await client.get(
                path, params={"path": "large.txt"}, headers={"Mcpcat-Key": WRITE}
            )
        ).json()
        assert large["reason"] == "too_large" and large["content"] is None
        for unsafe in ["../config.json", "/etc/passwd", "missing.txt"]:
            result = await client.get(
                path, params={"path": unsafe}, headers={"Mcpcat-Key": WRITE}
            )
            assert result.status_code in {404, 409}
        assert not marker.exists()


@pytest.mark.asyncio
async def test_corrupt_manifest_or_artifact_cannot_publish_or_download(apps_client):
    client, database = apps_client
    async with client:
        await upload(client)
        with UnitOfWork(database) as unit:
            row = unit.session.scalar(select(AppVersionRecord))
            row.manifest_json = {
                "files": [{"path": "../unexpected", "size": 1, "sha256": "0" * 64}]
            }
            unit.commit()
        prefix = "/api/apps/demo/versions/1.0.0"
        result = await client.post(
            f"{prefix}/publish", headers={"Mcpcat-Key": WRITE}, json={"reviewed": True}
        )
        assert result.status_code == 409
        assert (
            await client.get(f"{prefix}/download", headers={"Mcpcat-Key": WRITE})
        ).status_code == 409
        with UnitOfWork(database) as unit:
            artifact = unit.session.scalar(select(AppArtifactRecord))
            artifact.sha256 = "0" * 64
            unit.commit()
        assert (
            await client.get(f"{prefix}/download", headers={"Mcpcat-Key": WRITE})
        ).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    [
        {"csp": {"connectDomains": ["*"]}},
        {"csp": {"resourceDomains": ["https://example.com/path"]}},
        {"csp": {"connectDomains": ["https://name:secret@example.com"]}},
        {"csp": {"unknown": ["https://example.com"]}},
        {"permissions": {"sandbox": "allow-same-origin"}},
        {"permissions": {"camera": {"enabled": True}}},
    ],
)
async def test_unknown_or_expansive_review_config_is_rejected(apps_client, config):
    client, _ = apps_client
    async with client:
        await upload(client)
        response = await client.put(
            "/api/apps/demo/versions/1.0.0/config",
            headers={"Mcpcat-Key": WRITE},
            json=config,
        )
        assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_disabled_apps_management_and_anonymous_access_remain_closed(
    apps_client, monkeypatch
):
    client, _ = apps_client
    async with client:
        assert (await client.get("/api/apps")).status_code == 401
        monkeypatch.setenv("MCPCAT_APPS_ENABLED", "false")
        assert (
            await client.get("/api/apps", headers={"Mcpcat-Key": WRITE})
        ).status_code == 200
        assert (await upload(client)).status_code == 200


@pytest.mark.asyncio
async def test_binding_without_current_runtime_review_cannot_publish(apps_client):
    client, database = apps_client
    async with client:
        await upload(client)
        with UnitOfWork(database) as unit:
            row = unit.session.scalar(select(AppVersionRecord))
            unit.apps.bind_tool(
                version_id=row.id,
                alias="read",
                server_name="unknown",
                tool_name="value",
            )
            unit.commit()
        response = await client.post(
            "/api/apps/demo/versions/1.0.0/publish",
            headers={"Mcpcat-Key": WRITE},
            json={"reviewed": True},
        )
        assert response.status_code == 409
        assert (
            await client.get("/api/apps", headers={"Mcpcat-Key": READ})
        ).json() == []


@pytest.mark.asyncio
async def test_withdrawing_new_version_preserves_old_published_version(apps_client):
    client, _ = apps_client
    async with client:
        for version in ["1.0.0", "1.1.0"]:
            await upload(client, version=version)
            response = await client.post(
                f"/api/apps/demo/versions/{version}/publish",
                headers={"Mcpcat-Key": WRITE},
                json={"reviewed": True},
            )
            assert response.status_code == 200, response.text
        await client.post(
            "/api/apps/demo/versions/1.1.0/withdraw", headers={"Mcpcat-Key": WRITE}
        )
        listing = (await client.get("/api/apps", headers={"Mcpcat-Key": READ})).json()
        assert listing[0]["latest_version"] == "1.0.0"
        assert (
            await client.get(
                "/api/apps/demo/versions/1.0.0/download", headers={"Mcpcat-Key": READ}
            )
        ).status_code == 200
        assert (
            await client.get(
                "/api/apps/demo/versions/1.1.0/download", headers={"Mcpcat-Key": READ}
            )
        ).status_code == 404
