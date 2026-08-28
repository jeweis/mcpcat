"""Skills Registry 生命周期、版本和制品层测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.services.skill_artifact_service import (
    diagnose_artifacts,
    mark_mcp_source_missing,
    store_version_artifact,
)
from app.storage.database import Database
from app.storage.migrations import upgrade_schema
from app.storage.skill_repositories import (
    SkillDomainError,
    semver_key,
    validate_semver,
    validate_skill_slug,
)
from app.storage.unit_of_work import UnitOfWork


@pytest.fixture
def database(tmp_path: Path) -> Database:
    value = Database(tmp_path / "skills.db")
    upgrade_schema(value)
    yield value
    value.dispose()


def create_skill_version(database: Database, version: str = "1.0.0") -> tuple[int, int]:
    with UnitOfWork(database) as unit:
        skill = unit.skills.create(
            slug="weather-tools",
            display_name="Weather Tools",
            description="Weather lookup",
            source_type="mcp-generated",
            source_ref={"mcp_server": "weather"},
            created_by="admin",
        )
        version_row = unit.skill_versions.create(
            skill=skill,
            version=version,
            changelog="Initial",
            source_snapshot={"tools": ["forecast"]},
            compatibility={"mcporter": ">=0.7.3"},
            tool_schema_hash="a" * 64,
            generator_version="1.0.0",
            created_by="admin",
        )
        unit.commit()
        return skill.id, version_row.id


@pytest.mark.parametrize(
    "slug", ["Uppercase", "has space", "-leading", "trailing-", "a" * 65]
)
def test_rejects_invalid_skill_slugs(slug: str) -> None:
    with pytest.raises(SkillDomainError):
        validate_skill_slug(slug)


@pytest.mark.parametrize("version", ["1", "v1.0.0", "01.0.0", "1.0.0-01"])
def test_rejects_invalid_semver(version: str) -> None:
    with pytest.raises(SkillDomainError):
        validate_semver(version)


def test_semver_sorting_handles_prerelease() -> None:
    versions = ["1.0.0", "1.0.0-beta.2", "2.0.0", "1.0.0-beta.1"]
    assert sorted(versions, key=semver_key) == [
        "1.0.0-beta.1",
        "1.0.0-beta.2",
        "1.0.0",
        "2.0.0",
    ]


def test_unique_version_constraint_handles_concurrent_drafts(
    database: Database,
) -> None:
    skill_id, _version_id = create_skill_version(database)
    with pytest.raises(IntegrityError):
        with UnitOfWork(database) as unit:
            skill = unit.skills.get_by_slug("weather-tools")
            assert skill.id == skill_id
            unit.skill_versions.create(skill=skill, version="1.0.0")
            unit.commit()


def test_artifact_publish_visibility_and_immutability(
    database: Database, tmp_path: Path
) -> None:
    skill_id, version_id = create_skill_version(database)
    artifact_root = tmp_path / "artifacts"
    stored = store_version_artifact(
        database,
        skill_version_id=version_id,
        content=b"PK deterministic fixture",
        artifact_root=artifact_root,
    )
    assert stored.path.read_bytes() == b"PK deterministic fixture"
    assert list(artifact_root.rglob(".*.tmp")) == []

    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug("weather-tools")
        version = unit.skill_versions.get(skill_id, "1.0.0")
        unit.skill_versions.publish(skill, version)
        unit.commit()
    with UnitOfWork(database) as unit:
        assert [item.slug for item in unit.skills.list_visible()] == ["weather-tools"]
        version = unit.skill_versions.get(skill_id, "1.0.0")
        with pytest.raises(SkillDomainError, match="不可原地修改"):
            unit.skill_versions.update_draft(version, changelog="mutated")
    with pytest.raises(SkillDomainError, match="不可替换"):
        store_version_artifact(
            database,
            skill_version_id=version_id,
            content=b"different",
            artifact_root=artifact_root,
        )


def test_database_failure_cleans_new_artifact(
    database: Database, tmp_path: Path
) -> None:
    _skill_id, version_id = create_skill_version(database)
    with UnitOfWork(database) as unit:
        unit.skill_artifacts.create(
            skill_version_id=version_id,
            relative_path="weather-tools/1.0.0.zip",
            size=1,
            sha256="b" * 64,
        )
        unit.commit()

    root = tmp_path / "artifacts"
    with pytest.raises(IntegrityError):
        store_version_artifact(
            database,
            skill_version_id=version_id,
            content=b"new file",
            artifact_root=root,
        )
    assert not (root / "weather-tools/1.0.0.zip").exists()
    assert list(root.rglob(".*.tmp")) == []


def test_integrity_diagnostic_and_source_missing_history(
    database: Database, tmp_path: Path
) -> None:
    skill_id, version_id = create_skill_version(database)
    root = tmp_path / "artifacts"
    stored = store_version_artifact(
        database,
        skill_version_id=version_id,
        content=b"original",
        artifact_root=root,
    )
    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug("weather-tools")
        version = unit.skill_versions.get(skill_id, "1.0.0")
        unit.skill_versions.publish(skill, version)
        unit.commit()

    stored.path.write_bytes(b"tampered")
    report = diagnose_artifacts(database, root)
    assert report["problems"] == [
        {"relative_path": "weather-tools/1.0.0.zip", "status": "corrupt"}
    ]
    assert mark_mcp_source_missing(database, "weather") == 1
    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug("weather-tools")
        assert skill.status == "deprecated"
        assert skill.source_ref_json["source_status"] == "missing"
        assert unit.skill_versions.get(skill.id, "1.0.0") is not None


def test_bundle_repository_round_trip(database: Database) -> None:
    with UnitOfWork(database) as unit:
        bundle = unit.skill_bundles.create(
            snapshot_key="d" * 64,
            manifest={"skills": [{"slug": "weather-tools", "version": "1.0.0"}]},
            sha256="c" * 64,
            relative_path="bundles/all.zip",
        )
        unit.commit()
        bundle_id = bundle.id
    with UnitOfWork(database) as unit:
        restored = unit.skill_bundles.get_by_sha256("c" * 64)
        assert restored.id == bundle_id
