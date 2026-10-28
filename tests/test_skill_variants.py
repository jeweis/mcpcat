"""旧制品登记、共享引用和事务失败恢复测试。"""

import io
import zipfile

import pytest
from sqlalchemy import inspect, select

from alembic import command, op
from app.core.config import settings
from app.services.skill_package_validator import validate_skill_zip
from app.storage.database import Database
from app.storage.migrations import (
    create_alembic_config,
    get_schema_status,
    upgrade_schema,
)
from app.storage.runtime_identities import endpoint_identity, instance_identity
from app.storage.skill_variant_models import RuntimeIdentityRecord
from app.storage.unit_of_work import UnitOfWork


def seed_old(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "mcpcat_backups_path", str(tmp_path / "backups"))
    database = Database(tmp_path / "old.db")
    command.upgrade(create_alembic_config(database.path), "0003_apps_registry")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "demo/SKILL.md", "---\nname: demo\ndescription: Demo tasks.\n---\n# Demo\n"
        )
    # 真正模拟旧 schema 的记录，而不是在旧数据库上调用 v4 新业务代码。
    validation = validate_skill_zip(output.getvalue())
    with UnitOfWork(database) as unit:
        skill = unit.skills.create(
            slug="demo",
            display_name="Demo",
            description="Demo tasks.",
            source_type="uploaded",
        )
        version = unit.skill_versions.create(
            skill=skill,
            version="1.0.0",
            source_snapshot={
                "frontmatter": validation.metadata,
                "files": [entry.__dict__ for entry in validation.files],
            },
        )
        unit.session.flush()
        version_id = version.id
        unit.skill_artifacts.create(
            skill_version_id=version.id,
            relative_path="demo/1.0.0.zip",
            size=len(validation.normalized_zip),
            sha256=validation.sha256,
        )
        unit.commit()
    path = tmp_path / "artifacts" / "demo" / "1.0.0.zip"
    path.parent.mkdir(parents=True)
    path.write_bytes(validation.normalized_zip)
    return database, version_id, validation


def test_migration_references_original_local_bytes(tmp_path, monkeypatch):
    database, version_id, validation = seed_old(tmp_path, monkeypatch)
    path = next((tmp_path / "artifacts").rglob("*.zip"))
    before = path.read_bytes()
    upgrade_schema(database)
    with UnitOfWork(database) as unit:
        local = unit.skill_variants.get(version_id, "local")
        assert local.artifact_id == unit.skill_artifacts.get_for_version(version_id).id
        assert local.frontmatter_json == validation.metadata
        assert unit.skill_variants.get(version_id, "remote") is None
        identity = unit.session.get(RuntimeIdentityRecord, "instance").value
        unit.skill_variants.create(
            version_id=version_id,
            profile="remote",
            artifact_id=local.artifact_id,
            frontmatter=validation.metadata,
            manifest=local.manifest_json,
        )
        unit.commit()
    upgrade_schema(database)
    with UnitOfWork(database) as unit:
        assert unit.session.get(RuntimeIdentityRecord, "instance").value == identity
        assert len(unit.skill_variants.list_for_version(version_id)) == 2
        assert (
            unit.skill_variants.get(version_id, "remote").artifact_id
            == local.artifact_id
        )
    assert path.read_bytes() == before
    database.dispose()


def test_failed_schema_migration_rolls_back_all_tables(tmp_path, monkeypatch):
    database, _, _ = seed_old(tmp_path, monkeypatch)
    original = op.create_table

    def fail(name, *args, **kwargs):
        if name == "skill_content_variants":
            raise RuntimeError("injected DDL interruption")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(op, "create_table", fail)
    with pytest.raises(RuntimeError, match="interruption"):
        upgrade_schema(database)
    assert get_schema_status(database).current == "0003_apps_registry"
    assert "skill_remote_artifacts" not in inspect(database.engine).get_table_names()
    monkeypatch.setattr(op, "create_table", original)
    assert upgrade_schema(database).current == "0006_wecom_login"
    database.dispose()


def test_frozen_versions_cannot_gain_new_variants(tmp_path, monkeypatch):
    database, version_id, _ = seed_old(tmp_path, monkeypatch)
    upgrade_schema(database)
    with UnitOfWork(database) as unit:
        version = unit.skill_versions.get_by_id(version_id)
        version.status = "published"
        unit.commit()
        with pytest.raises(ValueError, match="新版本"):
            unit.skill_variants.create(version_id=version_id, profile="remote")
    database.dispose()


def test_old_draft_delete_cleans_variant_references(tmp_path, monkeypatch):
    database, version_id, _ = seed_old(tmp_path, monkeypatch)
    upgrade_schema(database)
    with UnitOfWork(database) as unit:
        version = unit.skill_versions.get_by_id(version_id)
        unit.skill_versions.delete_draft(version)
        unit.commit()
        assert unit.skill_variants.list_for_version(version_id) == []
    database.dispose()


def test_runtime_identities_survive_config_replace_and_restart(tmp_path, monkeypatch):
    database, _, _ = seed_old(tmp_path, monkeypatch)
    upgrade_schema(database)
    identity = instance_identity(database)
    endpoint = endpoint_identity(database, "service", "example")
    with UnitOfWork(database) as unit:
        unit.settings.set("app", {"public_base_url": "https://changed.invalid"})
        unit.commit()
    database.dispose()
    restarted = Database(tmp_path / "old.db")
    assert instance_identity(restarted) == identity
    assert endpoint_identity(restarted, "service", "example") == endpoint
    assert endpoint_identity(restarted, "service", "other") != endpoint
    restarted.dispose()
