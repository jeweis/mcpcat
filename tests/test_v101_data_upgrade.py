"""原 1.0.1 Schema/数据/API/制品升级等价性，不连接任何用户实例。"""

import hashlib

import httpx
import pytest
from legacy_v101_fixture import BASE_URL, make_old_fixture, snapshot
from test_skill_distribution import _ServerManager

from app.application import create_app
from app.core.config import settings
from app.services.config_service import ConfigService
from app.services.skill_distribution_service import (
    build_latest_bundle,
    resolve_bundle_download,
)
from app.storage.migrations import get_schema_status, upgrade_schema
from app.storage.unit_of_work import UnitOfWork


@pytest.fixture
def historical(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "mcpcat_backups_path", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "mcpcat_config_path", str(tmp_path / "absent.json"))
    monkeypatch.setattr(settings, "mcpcat_artifacts_path", str(tmp_path / "artifacts"))
    fixture = make_old_fixture(tmp_path)
    yield fixture
    ConfigService.reset_database()
    fixture[0].dispose()


def test_v101_upgrade_preserves_every_old_column_and_artifact(historical):
    database, artifacts, payload, before, index, bundle, files = historical
    assert get_schema_status(database).current == "0002_skills_registry"
    upgrade_schema(database)
    assert snapshot(database) == before
    ConfigService.configure_database(database)
    assert ConfigService.load_raw_config() == payload
    with UnitOfWork(database) as unit:
        for offset, status in enumerate(
            ("published", "draft", "deprecated", "archived"), 1
        ):
            variants = unit.skill_variants.list_for_version(200 + offset)
            assert len(variants) == 1
            local = variants[0]
            assert local.profile == "local" and local.binding == ""
            assert local.artifact_id == 300 + offset and local.status == status
            assert local.metadata_json == {"legacy_import": True}
            assert unit.skill_variants.get(200 + offset, "remote") is None
    cached = build_latest_bundle(database, base_url=BASE_URL, artifact_root=artifacts)
    assert cached.id == bundle.id and cached.sha256 == bundle.sha256
    assert (
        resolve_bundle_download(
            database, bundle_id=bundle.id, artifact_root=artifacts
        ).path.read_bytes()
        == files[bundle.path.relative_to(artifacts).as_posix()]
    )
    for relative, content in files.items():
        assert (artifacts / relative).read_bytes() == content
    again = snapshot(database)
    upgrade_schema(database)
    assert snapshot(database) == again == before


@pytest.mark.asyncio
async def test_v101_original_registry_and_download_routes_survive_app_upgrade(
    historical,
):
    database, artifacts, payload, before, index, bundle, files = historical
    application = create_app(database=database, server_manager=_ServerManager())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url=BASE_URL,
        headers={"X-Mcpcat-Key": "fixture-v101-admin-not-real"},
    ) as client:
        response = await client.get("/api/skills/registry")
        assert response.status_code == 200 and response.json() == index
        for entry in index["skills"]:
            response = await client.get(entry["download_url"])
            assert response.status_code == 200
            expected = files[f'{entry["slug"]}/1.0.1.zip']
            assert response.content == expected
            assert hashlib.sha256(response.content).hexdigest() == entry["sha256"]
        response = await client.get(f"/api/skills/bundles/{bundle.id}")
        assert response.status_code == 200
        assert response.content == files[bundle.path.relative_to(artifacts).as_posix()]
        assert hashlib.sha256(response.content).hexdigest() == bundle.sha256
    assert snapshot(database) == before
    assert ConfigService.load_raw_config() == payload
