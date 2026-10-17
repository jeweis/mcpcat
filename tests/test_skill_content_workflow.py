"""整批变体发布、独立 latest、共享历史字节与管理 API 回归。"""

import io
import zipfile
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.application import create_app
from app.core.config import settings
from app.services.config_service import ConfigService
from app.services.security_service import security_service
from app.services.skill_content_service import (
    clone_uploaded_for_remote,
    latest_variant,
    load_variant,
    store_remote_variant,
)
from app.services.skill_profile_upload import upload_skill_profiles
from app.services.skill_registry_service import (
    publish_skill_version,
    replace_draft_package,
    upload_skill,
)
from app.services.skill_variant_publication import publish_content_version
from app.storage.database import Database
from app.storage.skill_variant_models import SkillRemoteArtifactRecord
from app.storage.unit_of_work import UnitOfWork

READ = "content-fixture-read"
WRITE = "content-fixture-write"


class Manager:
    server_info = {}
    catalog_service = None

    def load_servers_from_config(self):
        pass

    def mount_all_servers(self, app):
        pass

    @asynccontextmanager
    async def create_unified_lifespan(self, app):
        yield


@pytest.fixture
def content_app(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "mcpcat_config_path", str(tmp_path / "config.json"))
    monkeypatch.setattr(settings, "mcpcat_artifacts_path", str(tmp_path / "artifacts"))
    monkeypatch.setattr(settings, "mcpcat_backups_path", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "mcpcat_default_admin_key", WRITE)
    monkeypatch.setattr(settings, "mcpcat_default_read_key", READ)
    security_service._first_run_keys = None
    database = Database(tmp_path / "content.db")
    app = create_app(database=database, server_manager=Manager())
    yield app, database
    ConfigService.reset_database()
    database.dispose()


def package(body=b"# Original\r\n", extras=None):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        archive.writestr(
            "demo/SKILL.md",
            b"---\r\nname: demo\r\ndescription: Demo tasks.\r\n---\r\n" + body,
        )
        for name, content in (extras or {}).items():
            archive.writestr("demo/" + name, content)
    return result.getvalue()


def baseline(database):
    version_id, validation = upload_skill(
        database, content=package(), version="1.0.0", changelog="", actor="fixture"
    )
    publish_skill_version(database, slug="demo", version="1.0.0", actor="fixture")
    return version_id, validation


def test_share_old_zip_without_copy_or_rewriting(content_app):
    _, database = content_app
    old_id, validation = baseline(database)
    new_id = clone_uploaded_for_remote(
        database, slug="demo", source_version="1.0.0", new_version="1.0.1"
    )
    with UnitOfWork(database) as unit:
        assert (
            unit.skill_variants.get(new_id, "remote").artifact_id
            == unit.skill_artifacts.get_for_version(old_id).id
        )
        assert unit.skill_artifacts.get_for_version(new_id).sha256 == validation.sha256
        assert list(unit.session.scalars(select(SkillRemoteArtifactRecord))) == []
    publish_content_version(database, slug="demo", version="1.0.1")
    local, bytes_local = load_variant(
        database, slug="demo", version="1.0.1", profile="local"
    )
    remote, bytes_remote = load_variant(
        database, slug="demo", version="1.0.1", profile="remote"
    )
    assert bytes_local == bytes_remote == validation.normalized_zip
    assert local["sha256"] == remote["sha256"] == validation.sha256
    assert latest_variant(database, slug="demo", profile="remote") == "1.0.1"


@pytest.mark.asyncio
async def test_remote_only_does_not_change_old_registry_detail_bundle(content_app):
    app, database = content_app
    baseline(database)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fixture",
        headers={"Mcpcat-Key": READ},
    ) as client:
        before = (await client.get("/api/skills/registry")).json()
        bundle = await client.get("/api/skills/bundle")
        upload_skill_profiles(
            database,
            content=package(b"# Remote\n"),
            version="1.0.1",
            selection="remote",
        )
        publish_content_version(database, slug="demo", version="1.0.1")
        assert (await client.get("/api/skills/registry")).json() == before
        assert (await client.get("/api/skills/bundle")).content == bundle.content
        detail = (await client.get("/api/skills/demo")).json()
        assert [row["version"] for row in detail["versions"]] == ["1.0.0"]
        assert (
            await client.get("/api/skills/demo/versions/1.0.1/download")
        ).status_code == 409
        variants = (await client.get("/api/skill-variants/demo")).json()
        assert [row["version"] for row in variants["versions"]] == ["1.0.1", "1.0.0"]
        assert variants["display_name"]
        assert variants["description"] == "Demo tasks."
        assert variants["source_type"] == "uploaded"
        assert "source" in variants
        summary = (await client.get("/api/skill-variants")).json()[0]
        assert summary["latest_published_version"] == "1.0.1"
        assert summary["source"] == variants["source"]
    assert latest_variant(database, slug="demo", profile="local") == "1.0.0"
    assert latest_variant(database, slug="demo", profile="remote") == "1.0.1"


@pytest.mark.asyncio
async def test_management_list_hides_new_remote_draft_from_read_key(content_app):
    app, database = content_app
    baseline(database)
    upload_skill_profiles(
        database,
        content=package(b"# Unreviewed\n"),
        version="1.0.1",
        selection="remote",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fixture",
        headers={"Mcpcat-Key": READ},
    ) as client:
        summary = (await client.get("/api/skill-variants")).json()[0]
        assert summary["latest_version"] == "1.0.0"
        assert summary["latest_published_version"] == "1.0.0"
        detail = (await client.get("/api/skill-variants/demo")).json()
        assert [row["version"] for row in detail["versions"]] == ["1.0.0"]
        response = await client.get(
            "/api/skill-variants", headers={"Mcpcat-Key": WRITE}
        )
        assert response.json()[0]["latest_version"] == "1.0.1"


def test_publish_batch_validates_all_content_and_freezes(content_app):
    _, database = content_app
    _, validation = baseline(database)
    _, version_id = upload_skill_profiles(
        database, content=package(), version="1.0.1", selection="both"
    )
    with UnitOfWork(database) as unit:
        remote = unit.skill_variants.get(version_id, "remote")
        assert (
            remote.artifact_id
            == unit.skill_variants.get(version_id, "local").artifact_id
        )
        remote.manifest_json = {
            "files": [{"path": "demo/SKILL.md", "size": 1, "sha256": "0" * 64}]
        }
        unit.commit()
    with pytest.raises(ValueError, match="清单"):
        publish_content_version(database, slug="demo", version="1.0.1")
    with UnitOfWork(database) as unit:
        assert unit.skill_versions.get_by_id(version_id).status == "draft"
        local = unit.skill_variants.get(version_id, "local")
        unit.skill_variants.get(version_id, "remote").manifest_json = (
            local.manifest_json
        )
        unit.commit()
    publish_content_version(database, slug="demo", version="1.0.1")
    with pytest.raises(ValueError, match="新版本"):
        store_remote_variant(
            database,
            version_id=version_id,
            binding="different",
            content=validation.normalized_zip,
        )


def test_missing_corrupt_artifact_blocks_publication_and_latest(content_app):
    _, database = content_app
    _, version_id = upload_skill_profiles(
        database, content=package(), version="1.0.0", selection="remote"
    )
    with UnitOfWork(database) as unit:
        variant = unit.skill_variants.get(version_id, "remote")
        artifact = unit.skill_variants.get_remote_artifact(variant.remote_artifact_id)
        from app.storage.database import resolve_storage_path

        path = (
            resolve_storage_path(settings.mcpcat_artifacts_path)
            / artifact.relative_path
        )
    original = path.read_bytes()
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="完整性"):
        publish_content_version(database, slug="demo", version="1.0.0")
    path.write_bytes(original)
    publish_content_version(database, slug="demo", version="1.0.0")
    assert latest_variant(database, slug="demo", profile="remote") == "1.0.0"
    path.unlink()
    assert latest_variant(database, slug="demo", profile="remote") is None
    with pytest.raises(ValueError, match="缺失"):
        load_variant(database, slug="demo", version="1.0.0", profile="remote")
    path.write_bytes(original)
    assert latest_variant(database, slug="demo", profile="remote") == "1.0.0"
    assert (
        load_variant(database, slug="demo", version="1.0.0", profile="remote")[1]
        == original
    )


def test_incomplete_expected_profile_cannot_publish(content_app):
    _, database = content_app
    version_id, _ = upload_skill(
        database, content=package(), version="1.0.0", changelog="", actor=None
    )
    with UnitOfWork(database) as unit:
        row = unit.skill_versions.get_by_id(version_id)
        row.source_snapshot_json = {
            **row.source_snapshot_json,
            "content_profiles": ["local", "remote"],
        }
        unit.commit()
    with pytest.raises(ValueError, match="批次尚未完成"):
        publish_content_version(database, slug="demo", version="1.0.0")


def test_oversize_remote_does_not_block_legacy_local(content_app):
    _, database = content_app
    content = package(extras={f"ref/{index}.md": b"reference" for index in range(512)})
    with pytest.raises(ValueError, match="512"):
        upload_skill_profiles(
            database, content=content, version="1.0.0", selection="both"
        )
    upload_skill_profiles(database, content=content, version="1.0.0", selection="local")
    publish_content_version(database, slug="demo", version="1.0.0")
    assert latest_variant(database, slug="demo", profile="local") == "1.0.0"


def test_protocol_bare_and_slash_paths_have_same_read_permission():
    from app.middleware.auth import AuthMiddleware
    from app.models.mcp_config import PermissionType

    middleware = AuthMiddleware(lambda scope, receive, send: None)
    assert (
        middleware.get_required_permission("/mcp/skills", "POST") == PermissionType.READ
    )
    assert (
        middleware.get_required_permission("/mcp/skills/", "POST")
        == PermissionType.READ
    )
    assert (
        middleware.get_required_permission("/api/skill-variants/upload", "POST")
        == PermissionType.WRITE
    )


@pytest.mark.asyncio
async def test_remote_withdraw_keeps_local_and_requires_write(content_app):
    app, database = content_app
    upload_skill_profiles(
        database, content=package(), version="1.0.0", selection="both"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        published = await client.post(
            "/api/skill-variants/demo/versions/1.0.0/publish",
            headers={"Mcpcat-Key": WRITE},
        )
        assert published.status_code == 200
        with UnitOfWork(database) as unit:
            version = unit.skill_versions.get(
                unit.skills.get_by_slug("demo").id, "1.0.0"
            )
            assert (
                unit.skill_variants.get(version.id, "remote").metadata_json[
                    "public_remote"
                ]
                is False
            )
        endpoint = "/api/skill-variants/demo/versions/1.0.0/withdraw-remote"
        assert (
            await client.post(endpoint, headers={"Mcpcat-Key": READ})
        ).status_code == 403
        assert (
            await client.post(endpoint, headers={"Mcpcat-Key": WRITE})
        ).status_code == 200
        assert (
            await client.get(
                "/api/skills/demo/versions/1.0.0/download", headers={"Mcpcat-Key": READ}
            )
        ).status_code == 200
    assert latest_variant(database, slug="demo", profile="remote") is None
    with pytest.raises(ValueError, match="撤销"):
        load_variant(database, slug="demo", version="1.0.0", profile="remote")


@pytest.mark.asyncio
async def test_public_remote_requires_explicit_publication_review(content_app):
    app, database = content_app
    upload_skill_profiles(
        database, content=package(), version="1.0.0", selection="remote"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        assert (
            await client.post(
                "/api/skill-variants/demo/versions/1.0.0/publish",
                json={"remote_public": True},
                headers={"Mcpcat-Key": WRITE},
            )
        ).status_code == 200
    info, _ = load_variant(database, slug="demo", version="1.0.0", profile="remote")
    assert info["metadata"]["public_remote"] is True


@pytest.mark.asyncio
async def test_connections_do_not_advertise_an_unmounted_endpoint(content_app):
    app, database = content_app
    upload_skill_profiles(
        database, content=package(), version="1.0.0", selection="remote"
    )
    publish_content_version(database, slug="demo", version="1.0.0")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fixture",
        headers={"Mcpcat-Key": READ},
    ) as client:
        data = (await client.get("/api/skill-variants/demo/connections")).json()
    assert data["connections"][0]["ready"] is False
    assert data["connections"][0]["endpoint_url"] is None


@pytest.mark.asyncio
async def test_upload_management_returns_validated_manifest_and_changelog(content_app):
    app, database = content_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fixture",
        headers={"Mcpcat-Key": WRITE},
    ) as client:
        options = [
            {"kind": "independent", "source_name": "", "label": "独立 Skills 端点"}
        ]
        if app.state.server_manager.catalog_service is not None:
            options.append(
                {"kind": "catalog", "source_name": "", "label": "工具搜索 Catalog"}
            )
        assert (
            await client.get("/api/skill-variants/bindings/available")
        ).json() == options
        response = await client.post(
            "/api/skill-variants/upload",
            files={
                "file": (
                    "demo.zip",
                    package(
                        extras={
                            "scripts/demo.py": b"raise RuntimeError('never execute')"
                        }
                    ),
                    "application/zip",
                )
            },
            data={
                "version": "1.0.0",
                "selection": "both",
                "changelog": "Keep original files",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data["sha256"]) == 64
        assert {item["path"] for item in data["files"]} == {
            "demo/SKILL.md",
            "demo/scripts/demo.py",
        }
        assert data["scripts"] == ["demo/scripts/demo.py"]
        detail = (await client.get("/api/skill-variants/demo")).json()
        assert detail["versions"][0]["changelog"] == "Keep original files"
    local, local_bytes = load_variant(
        database, slug="demo", version="1.0.0", profile="local", include_drafts=True
    )
    remote, remote_bytes = load_variant(
        database, slug="demo", version="1.0.0", profile="remote", include_drafts=True
    )
    assert local_bytes == remote_bytes
    assert local["sha256"] == remote["sha256"] == data["sha256"]


@pytest.mark.asyncio
async def test_unavailable_upload_binding_does_not_create_draft(content_app):
    app, database = content_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fixture",
        headers={"Mcpcat-Key": WRITE},
    ) as client:
        response = await client.post(
            "/api/skill-variants/upload",
            files={"file": ("demo.zip", package(), "application/zip")},
            data={
                "version": "1.0.0",
                "selection": "remote",
                "binding_kind": "service",
                "source_name": "stopped",
            },
        )
        assert response.status_code == 409
    with UnitOfWork(database) as unit:
        assert unit.skills.get_by_slug("demo") is None


@pytest.mark.asyncio
async def test_upload_binding_options_exclude_stopped_and_never_return_secrets(
    content_app,
):
    app, _ = content_app
    app.state.server_manager.server_info = {
        "live": {"status": "running", "headers": {"Authorization": "fixture-secret"}},
        "stopped": {"status": "stopped"},
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fixture",
        headers={"Mcpcat-Key": WRITE},
    ) as client:
        response = await client.get("/api/skill-variants/bindings/available")
    choices = response.json()
    assert {row["source_name"] for row in choices if row["kind"] == "service"} == {
        "live"
    }
    assert "fixture-secret" not in response.text
    assert all(set(row) == {"kind", "source_name", "label"} for row in choices)


def test_latest_binding_never_falls_back_to_another_endpoint(content_app):
    _, database = content_app
    upload_skill_profiles(
        database,
        content=package(),
        version="1.0.0",
        selection="remote",
        binding="endpoint-a",
    )
    publish_content_version(database, slug="demo", version="1.0.0")
    upload_skill_profiles(
        database,
        content=package(b"# Other endpoint\n"),
        version="1.0.1",
        selection="remote",
        binding="endpoint-b",
    )
    publish_content_version(database, slug="demo", version="1.0.1")
    assert (
        latest_variant(database, slug="demo", profile="remote", binding="endpoint-a")
        == "1.0.0"
    )
    assert (
        latest_variant(database, slug="demo", profile="remote", binding="endpoint-b")
        == "1.0.1"
    )
    assert latest_variant(database, slug="demo", profile="remote") is None
    with pytest.raises(ValueError, match="缺少"):
        load_variant(
            database,
            slug="demo",
            version="1.0.1",
            profile="remote",
            binding="endpoint-a",
        )


def test_concurrent_edit_and_publish_never_publish_half_snapshot(content_app):
    from concurrent.futures import ThreadPoolExecutor

    _, database = content_app
    upload_skill_profiles(
        database, content=package(), version="1.0.0", selection="both"
    )

    def edit():
        try:
            replace_draft_package(
                database,
                slug="demo",
                version="1.0.0",
                actor="editor",
                content=package(b"# Edited\n", extras={"new.md": b"new reference"}),
                changelog="edited",
            )
        except ValueError:
            # 发布先取得锁时编辑被明确拒绝，而非悄悄覆盖冻结版本。
            pass

    with ThreadPoolExecutor(max_workers=2) as executor:
        editor = executor.submit(edit)
        publisher = executor.submit(
            publish_content_version, database, slug="demo", version="1.0.0"
        )
        editor.result(timeout=10)
        publisher.result(timeout=10)
    local, content = load_variant(
        database, slug="demo", version="1.0.0", profile="local"
    )
    remote, remote_content = load_variant(
        database, slug="demo", version="1.0.0", profile="remote"
    )
    assert content == remote_content
    assert local["manifest"] == remote["manifest"]
    with UnitOfWork(database) as unit:
        row = unit.skill_versions.get(unit.skills.get_by_slug("demo").id, "1.0.0")
        assert row.source_snapshot_json["files"] == local["manifest"]["files"]


@pytest.mark.asyncio
async def test_script_upload_publish_preview_and_read_are_non_executing(
    content_app, tmp_path
):
    app, database = content_app
    marker = tmp_path / "never-execute"
    malicious = {
        "scripts/test.py": f"open({str(marker)!r}, 'w').write('bad')".encode(),
        "scripts/test.js": f"require('fs').writeFileSync({str(marker)!r}, 'bad')".encode(),
        "scripts/test.sh": f"echo bad > {str(marker)!r}".encode(),
    }
    upload_skill_profiles(
        database, content=package(extras=malicious), version="1.0.0", selection="both"
    )
    publish_content_version(database, slug="demo", version="1.0.0")
    from app.services.skill_variant_artifacts import read_manifest_file

    for profile in ("local", "remote"):
        info, raw = load_variant(
            database, slug="demo", version="1.0.0", profile=profile
        )
        for path, value in malicious.items():
            assert read_manifest_file(raw, info["manifest"], "demo/" + path) == value
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fixture",
        headers={"Mcpcat-Key": READ},
    ) as client:
        for path, value in malicious.items():
            response = await client.get(
                "/api/skill-variants/demo/versions/1.0.0/file",
                params={"profile": "remote", "path": "demo/" + path},
            )
            assert (
                response.status_code == 200
                and response.json()["content"] == value.decode()
            )
    assert not marker.exists()


def test_edit_shared_new_local_does_not_modify_old_or_remote(content_app):
    _, database = content_app
    _, original = baseline(database)
    clone_uploaded_for_remote(
        database, slug="demo", source_version="1.0.0", new_version="1.0.1"
    )
    replace_draft_package(
        database,
        slug="demo",
        version="1.0.1",
        content=package(b"# Edited\n"),
        changelog="edited",
        actor="fixture",
    )
    _, old_bytes = load_variant(database, slug="demo", version="1.0.0", profile="local")
    _, remote_bytes = load_variant(
        database, slug="demo", version="1.0.1", profile="remote", include_drafts=True
    )
    _, local_bytes = load_variant(
        database, slug="demo", version="1.0.1", profile="local", include_drafts=True
    )
    assert old_bytes == remote_bytes == original.normalized_zip
    assert local_bytes != remote_bytes


@pytest.mark.asyncio
async def test_management_mode_upload_permissions_and_previews(content_app):
    app, database = content_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        endpoint = "/api/skill-variants/upload"
        data = {"version": "1.0.0", "selection": "both"}
        content = package(extras={"large.md": b"x" * 600000, "file.bin": b"\xff\x00"})
        assert (
            await client.post(
                endpoint,
                data=data,
                files={"file": ("demo.zip", content)},
                headers={"Mcpcat-Key": READ},
            )
        ).status_code == 403
        uploaded = await client.post(
            endpoint,
            data=data,
            files={"file": ("demo.zip", content)},
            headers={"Mcpcat-Key": WRITE},
        )
        assert uploaded.status_code == 200
        path = "/api/skill-variants/demo/versions/1.0.0/file"
        query = {"profile": "remote", "path": "demo/SKILL.md"}
        assert (
            await client.get(path, params=query, headers={"Mcpcat-Key": READ})
        ).status_code == 409
        md = (
            await client.get(path, params=query, headers={"Mcpcat-Key": WRITE})
        ).json()
        assert md["is_markdown"] and "\r\n" in md["content"]
        query["path"] = "demo/large.md"
        assert (
            await client.get(path, params=query, headers={"Mcpcat-Key": WRITE})
        ).json()["reason"] == "too_large"
        query["path"] = "demo/file.bin"
        assert (
            await client.get(path, params=query, headers={"Mcpcat-Key": WRITE})
        ).json()["reason"] == "binary"
        query["path"] = "../outside"
        assert (
            await client.get(path, params=query, headers={"Mcpcat-Key": WRITE})
        ).status_code == 409
