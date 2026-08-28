"""Skill 分发 API 与制品快照的端到端契约测试。"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator

import httpx
import pytest

from app.application import create_app
from app.core.config import settings
from app.services.config_service import ConfigService
from app.services.security_service import security_service
from app.services.skill_distribution_service import build_latest_bundle
from app.services.skill_registry_service import publish_skill_version, upload_skill
from app.storage.database import Database
from app.storage.skill_repositories import SkillDomainError
from app.storage.unit_of_work import UnitOfWork

READ_KEY = "read-api-key-for-distribution-test"
WRITE_KEY = "write-api-key-for-distribution-test"
OAUTH_TOKEN = "oauth-access-token-for-distribution-test"
CLIENT_SECRET = "client-secret-for-distribution-test"
DATABASE_SECRET = "database-secret-for-distribution-test"


class _ServerManager:
    """满足应用工厂所需的最小 ServerManager 协议。"""

    catalog_service = None

    def load_servers_from_config(self) -> None:
        pass

    def mount_all_servers(self, _app) -> None:
        pass

    @asynccontextmanager
    async def create_unified_lifespan(self, _app):
        yield


@pytest.fixture
def distribution_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[object, Database, Path]]:
    artifacts = tmp_path / "artifacts"
    monkeypatch.setattr(settings, "mcpcat_config_path", str(tmp_path / "config.json"))
    monkeypatch.setattr(settings, "mcpcat_backups_path", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "mcpcat_artifacts_path", str(artifacts))
    monkeypatch.setattr(settings, "mcpcat_default_read_key", READ_KEY)
    monkeypatch.setattr(settings, "mcpcat_default_admin_key", WRITE_KEY)
    monkeypatch.setattr(settings, "mcpcat_secret_key", DATABASE_SECRET)
    security_service._first_run_keys = None

    database = Database(tmp_path / "distribution.db", busy_timeout_ms=10_000)
    application = create_app(database=database, server_manager=_ServerManager())
    ConfigService.add_server_to_config(
        "secret-source",
        {
            "type": "streamable-http",
            "url": "https://example.test/mcp",
            "oauth": {
                "client_secret": CLIENT_SECRET,
                "token": {"access_token": OAUTH_TOKEN},
            },
            "database_secret": DATABASE_SECRET,
        },
    )
    yield application, database, artifacts
    ConfigService.reset_database()
    database.dispose()


def _skill_zip(slug: str, *, body: str = "# Skill\n") -> bytes:
    skill = (
        "---\n"
        f"name: {slug}\n"
        f"description: {slug} distribution test skill.\n"
        "---\n"
        f"{body}"
    ).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{slug}/SKILL.md", skill)
        archive.writestr(f"{slug}/references/guide.md", b"public reference\n")
    return output.getvalue()


def _upload(
    database: Database,
    artifacts: Path,
    slug: str,
    version: str,
    *,
    publish: bool = False,
) -> str:
    _, validation = upload_skill(
        database,
        content=_skill_zip(slug, body=f"# {slug} {version}\n"),
        version=version,
        changelog=f"release {version}",
        actor="distribution-test",
        artifact_root=artifacts,
    )
    if publish:
        publish_skill_version(
            database, slug=slug, version=version, actor="distribution-test"
        )
    return validation.sha256


def _zip_members(content: bytes) -> tuple[set[str], dict[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = set(archive.namelist())
        return names, {
            name: archive.read(name) for name in names if not name.endswith("/")
        }


def _assert_no_secret(payloads: list[bytes]) -> None:
    for payload in payloads:
        for secret in (
            READ_KEY,
            WRITE_KEY,
            OAUTH_TOKEN,
            CLIENT_SECRET,
            DATABASE_SECRET,
        ):
            assert secret.encode() not in payload


@pytest.mark.asyncio
async def test_bootstrap_registry_permissions_download_and_secret_redaction(
    distribution_app: tuple[object, Database, Path],
) -> None:
    application, database, artifacts = distribution_app
    published_sha = _upload(
        database, artifacts, "published-skill", "1.0.0", publish=True
    )
    _upload(database, artifacts, "published-skill", "2.0.0")
    _upload(database, artifacts, "draft-only", "1.0.0")

    headers = {"Mcpcat-Key": READ_KEY}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        bootstrap = await client.get("/api/skills/bootstrap")
        assert bootstrap.status_code == 200
        assert bootstrap.json() == {
            "instance_name": application.title,
            "base_url": "http://test",
            "api_version": "v1",
            "registry_schema_version": "1.0.0",
            "auth_header_name": "Mcpcat-Key",
            "registry_path": "/api/skills/registry",
            "min_cli_version": "0.1.0",
            "recommended_cli_version": "0.1.0",
        }

        read_list = await client.get("/api/skills", headers=headers)
        write_list = await client.get("/api/skills", headers={"Mcpcat-Key": WRITE_KEY})
        assert [item["slug"] for item in read_list.json()] == ["published-skill"]
        assert [item["slug"] for item in write_list.json()] == [
            "draft-only",
            "published-skill",
        ]

        read_detail = await client.get("/api/skills/published-skill", headers=headers)
        write_detail = await client.get(
            "/api/skills/published-skill", headers={"Mcpcat-Key": WRITE_KEY}
        )
        assert [item["version"] for item in read_detail.json()["versions"]] == ["1.0.0"]
        assert [item["version"] for item in write_detail.json()["versions"]] == [
            "2.0.0",
            "1.0.0",
        ]

        registry = await client.get("/api/skills/registry", headers=headers)
        assert registry.status_code == 200
        assert registry.headers["etag"].startswith('"')
        assert registry.json()["skills"] == [
            {
                "slug": "published-skill",
                "display_name": "published-skill",
                "description": "published-skill distribution test skill.",
                "source_type": "uploaded",
                "status": "published",
                "latest_published_version": "1.0.0",
                "compatibility": {},
                "sha256": published_sha,
                "size": len(
                    _skill_zip("published-skill", body="# published-skill 1.0.0\n")
                ),
                "download_url": "http://test/api/skills/published-skill/versions/1.0.0/download",
            }
        ]
        not_modified = await client.get(
            "/api/skills/registry",
            headers={**headers, "If-None-Match": registry.headers["etag"]},
        )
        assert not_modified.status_code == 304
        assert not_modified.headers["etag"] == registry.headers["etag"]
        assert not_modified.content == b""

        incompatible = await client.get(
            "/api/skills/registry",
            headers={**headers, "X-Mcpcat-CLI-Version": "0.0.9"},
        )
        assert incompatible.status_code == 426
        assert incompatible.json()["detail"]["min_cli_version"] == "0.1.0"

        download = await client.get(
            "/api/skills/published-skill/versions/1.0.0/download", headers=headers
        )
        assert download.status_code == 200
        assert download.headers["content-type"].startswith("application/zip")
        assert download.headers["content-disposition"] == (
            'attachment; filename="published-skill-1.0.0.zip"'
        )
        assert download.headers["x-skill-version"] == "1.0.0"
        assert download.headers["x-checksum-sha256"] == published_sha
        assert hashlib.sha256(download.content).hexdigest() == published_sha

    _assert_no_secret(
        [
            bootstrap.content,
            read_list.content,
            write_list.content,
            read_detail.content,
            write_detail.content,
            registry.content,
            download.content,
            *_zip_members(download.content)[1].values(),
        ]
    )


@pytest.mark.asyncio
async def test_bundle_cache_history_concurrency_integrity_and_secret_redaction(
    distribution_app: tuple[object, Database, Path],
) -> None:
    application, database, artifacts = distribution_app
    _upload(database, artifacts, "alpha-skill", "1.0.0", publish=True)
    _upload(database, artifacts, "bravo-skill", "1.0.0", publish=True)
    headers = {"Mcpcat-Key": READ_KEY}

    with ThreadPoolExecutor(max_workers=4) as executor:
        concurrent = list(
            executor.map(
                lambda _index: build_latest_bundle(
                    database, base_url="http://test", artifact_root=artifacts
                ),
                range(4),
            )
        )
    assert {bundle.id for bundle in concurrent} == {concurrent[0].id}
    assert {bundle.sha256 for bundle in concurrent} == {concurrent[0].sha256}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        first = await client.get("/api/skills/bundle", headers=headers)
        second = await client.get("/api/skills/bundle", headers=headers)
        assert first.status_code == second.status_code == 200
        assert first.headers["x-bundle-id"] == second.headers["x-bundle-id"]
        assert first.headers["x-checksum-sha256"] == second.headers["x-checksum-sha256"]
        assert first.content == second.content
        assert (
            hashlib.sha256(first.content).hexdigest()
            == first.headers["x-checksum-sha256"]
        )
        assert first.headers["content-disposition"] == (
            f'attachment; filename="mcpcat-skills-{first.headers["x-bundle-id"]}.zip"'
        )
        first_id = int(first.headers["x-bundle-id"])
        names, files = _zip_members(first.content)
        assert {
            "alpha-skill/SKILL.md",
            "bravo-skill/SKILL.md",
            ".mcpcat-bundle.json",
        } <= names
        manifest = json.loads(files[".mcpcat-bundle.json"])
        assert manifest["bundle_id"] == concurrent[0].manifest["bundle_id"]
        assert {(item["slug"], item["version"]) for item in manifest["skills"]} == {
            ("alpha-skill", "1.0.0"),
            ("bravo-skill", "1.0.0"),
        }

        _upload(database, artifacts, "alpha-skill", "2.0.0", publish=True)
        latest = await client.get("/api/skills/bundle", headers=headers)
        assert latest.status_code == 200
        assert latest.headers["x-bundle-id"] != str(first_id)

        historical = await client.get(
            f"/api/skills/bundles/{first_id}", headers=headers
        )
        assert historical.status_code == 200
        assert historical.headers["x-bundle-id"] == str(first_id)
        assert (
            historical.headers["x-checksum-sha256"]
            == first.headers["x-checksum-sha256"]
        )
        assert historical.content == first.content

    _assert_no_secret(
        [first.content, latest.content, historical.content, *files.values()]
    )

    corrupt_sha = _upload(database, artifacts, "corrupt-skill", "1.0.0", publish=True)
    artifact_path = artifacts / "corrupt-skill" / "1.0.0.zip"
    assert hashlib.sha256(artifact_path.read_bytes()).hexdigest() == corrupt_sha
    artifact_path.write_bytes(b"damaged artifact")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        for _ in range(2):
            blocked = await client.get(
                "/api/skills/corrupt-skill/versions/1.0.0/download", headers=headers
            )
            assert blocked.status_code == 409
            assert "完整性校验失败" in blocked.json()["detail"]

    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug("corrupt-skill")
        version = unit.skill_versions.get(skill.id, "1.0.0")
        assert (
            unit.skill_artifacts.get_for_version(version.id).integrity_status
            == "corrupt"
        )


def test_resolve_version_download_blocks_missing_or_corrupt_artifact(
    distribution_app: tuple[object, Database, Path],
) -> None:
    """服务函数也必须在每次请求时核对磁盘制品，而非仅信任数据库。"""

    _application, database, artifacts = distribution_app
    _upload(database, artifacts, "service-check", "1.0.0", publish=True)
    (artifacts / "service-check" / "1.0.0.zip").unlink()

    from app.services.skill_distribution_service import resolve_version_download

    with pytest.raises(SkillDomainError, match="完整性校验失败"):
        resolve_version_download(
            database,
            slug="service-check",
            version="1.0.0",
            artifact_root=artifacts,
        )
    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug("service-check")
        version = unit.skill_versions.get(skill.id, "1.0.0")
        assert (
            unit.skill_artifacts.get_for_version(version.id).integrity_status
            == "missing"
        )
