"""升级中断、幂等、快照恢复与缺失/损坏旧制品的失败关闭。"""

import hashlib
import shutil

import httpx
import pytest
from legacy_v101_fixture import BASE_URL, snapshot
from sqlalchemy import inspect, select
from sqlalchemy.engine import Connection
from test_skill_distribution import _ServerManager
from test_v101_data_upgrade import historical

from app.application import create_app
from app.services.config_service import ConfigService
from app.services.skill_artifact_service import diagnose_artifacts
from app.services.skill_distribution_service import build_registry_index
from app.storage.database import Database
from app.storage.migrations import get_schema_status, upgrade_schema
from app.storage.skill_variant_models import RuntimeIdentityRecord
from app.storage.unit_of_work import UnitOfWork


def test_interrupted_v101_upgrade_never_starts_business_and_can_retry(
    historical, tmp_path, monkeypatch
):
    database, artifacts, payload, before, index, bundle, files = historical
    original = Connection.execute
    started = []

    def interrupted(connection, statement, *args, **kwargs):
        if (
            getattr(statement, "is_insert", False)
            and statement.table.name == "runtime_identities"
        ):
            raise RuntimeError("injected failure after variant backfill")
        return original(connection, statement, *args, **kwargs)

    manager = _ServerManager()
    monkeypatch.setattr(
        manager, "load_servers_from_config", lambda: started.append(True)
    )
    monkeypatch.setattr(Connection, "execute", interrupted)
    with pytest.raises(RuntimeError, match="injected failure"):
        create_app(database=database, server_manager=manager)
    assert not started
    assert get_schema_status(database).current == "0002_skills_registry"
    tables = set(inspect(database.engine).get_table_names())
    assert not {"apps", "skill_content_variants", "runtime_identities"} & tables
    assert snapshot(database) == before
    backups = list((tmp_path / "backups").glob("*pre-schema-upgrade*.db"))
    assert len(backups) == 1
    original_backup = backups[0].read_bytes()
    restore = tmp_path / "restored-1.0.1.db"
    shutil.copy2(backups[0], restore)
    restored = Database(restore)
    try:
        assert get_schema_status(restored).current == "0002_skills_registry"
        assert snapshot(restored) == before
    finally:
        restored.dispose()
    monkeypatch.setattr(Connection, "execute", original)
    upgrade_schema(database)
    assert snapshot(database) == before and backups[0].read_bytes() == original_backup
    for relative, content in files.items():
        assert (artifacts / relative).read_bytes() == content


def test_repeated_v101_upgrade_keeps_variants_identities_and_backups(
    historical, tmp_path
):
    database = historical[0]
    upgrade_schema(database)
    before = snapshot(database)
    with UnitOfWork(database) as unit:
        identities = {
            row.key: row.value
            for row in unit.session.scalars(select(RuntimeIdentityRecord))
        }
        variant_ids = [
            row.id
            for offset in range(1, 5)
            for row in unit.skill_variants.list_for_version(200 + offset)
        ]
    backups = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (tmp_path / "backups").glob("*.db")
    }
    for _ in range(3):
        upgrade_schema(database)
    assert snapshot(database) == before
    with UnitOfWork(database) as unit:
        assert {
            row.key: row.value
            for row in unit.session.scalars(select(RuntimeIdentityRecord))
        } == identities
        assert [
            row.id
            for offset in range(1, 5)
            for row in unit.skill_variants.list_for_version(200 + offset)
        ] == variant_ids
    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (tmp_path / "backups").glob("*.db")
    } == backups


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["skill", "bundle"])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
async def test_old_artifact_damage_blocks_download_until_matching_file_restored(
    historical, kind, damage
):
    database, artifacts, payload, before, index, bundle, files = historical
    path = artifacts / "old-published/1.0.1.zip" if kind == "skill" else bundle.path
    original = path.read_bytes()
    if damage == "missing":
        path.unlink()
    else:
        changed = bytearray(original)
        changed[len(changed) // 2] ^= 1  # 同 size 的损坏也必须由 SHA 拦截。
        path.write_bytes(changed)
    application = create_app(database=database, server_manager=_ServerManager())
    route = (
        "/api/skills/old-published/versions/1.0.1/download"
        if kind == "skill"
        else f"/api/skills/bundles/{bundle.id}"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url=BASE_URL,
        headers={"X-Mcpcat-Key": "fixture-v101-admin-not-real"},
    ) as client:
        response = await client.get(route)
        assert response.status_code == 409 and "完整性" in response.json()["detail"]
        if kind == "skill":
            with UnitOfWork(database) as unit:
                assert (
                    unit.skill_artifacts.get_for_version(201).integrity_status == damage
                )
            assert "old-published" not in {
                entry["slug"]
                for entry in build_registry_index(database, base_url=BASE_URL)["skills"]
            }
        path.write_bytes(original)
        diagnose_artifacts(database, artifacts)
        response = await client.get(route)
        assert response.status_code == 200 and response.content == original
        assert build_registry_index(database, base_url=BASE_URL) == index
    assert snapshot(database) == before
    assert ConfigService.load_raw_config() == payload
