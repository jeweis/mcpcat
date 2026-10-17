"""以 v1.0.1 的原始两份 DDL 构造有历史状态/凭证的隔离迁移夹具。"""

import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

from test_legacy_migration import legacy_payload

from alembic import command
from app.services.skill_distribution_service import (
    build_latest_bundle,
    build_registry_index,
)
from app.services.skill_package_validator import (
    SkillPackageValidation,
    validate_skill_zip,
)
from app.storage.database import Database
from app.storage.legacy_migration import migrate_legacy_config
from app.storage.migrations import create_alembic_config
from app.storage.unit_of_work import UnitOfWork

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://fixture"
OLD_DDL_HASHES = {
    "0001_initial_storage.py": "84b7e76d86194b8ae7f552d37a805872f0bdd05b2e8bcc36ee9ef4918502a7f5",
    "0002_skills_registry.py": "1bcca3b8869e29ec0bee9d0cce6853fb51f0669875e9c691783d5a239f6a15ad",
}
OLD_TABLES = (
    "api_keys",
    "mcp_servers",
    "system_settings",
    "migration_history",
    "skills",
    "skill_versions",
    "skill_artifacts",
    "skill_bundles",
    "skill_audit_events",
)


def old_schema(database: Database) -> None:
    """哈希固定自本地 Git v1.0.1；历史 DDL 不能靠改写旧迁移适配新模型。"""
    for name, digest in OLD_DDL_HASHES.items():
        assert (
            hashlib.sha256((ROOT / "alembic/versions" / name).read_bytes()).hexdigest()
            == digest
        )
    command.upgrade(create_alembic_config(database.path), "0002_skills_registry")


def old_zip(slug: str) -> SkillPackageValidation:
    """包含客户本地脚本和二进制；构造/迁移均不执行。"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            f"{slug}/SKILL.md",
            f"---\nname: {slug}\ndescription: Historical fixture tasks.\n---\n# 原有内容\n",
        )
        archive.writestr(
            f"{slug}/scripts/task.py", "raise RuntimeError('must never execute')\n"
        )
        archive.writestr(f"{slug}/assets/data.bin", b"\x00\xff\x01legacy")
    return validate_skill_zip(output.getvalue())


def seed_skill(database: Database, artifacts: Path, offset: int, status: str) -> None:
    """只写 v1.0.1 字段；显式稀疏 ID、未知嵌套字段和各发布状态。"""
    slug = "old-" + status
    validation = old_zip(slug)
    with UnitOfWork(database) as unit:
        skill = unit.skills.create(
            slug=slug,
            display_name=slug,
            description="历史用途",
            source_type="uploaded" if offset % 2 else "mcp-generated",
            source_ref={"legacy_unknown": {"keep": [offset]}, "server_name": "remote"},
        )
        skill.id = 100 + offset
        version = unit.skill_versions.create(
            skill=skill,
            version="1.0.1",
            changelog="原始版本",
            source_snapshot={
                "frontmatter": validation.metadata,
                "files": [entry.__dict__ for entry in validation.files],
                "unknown_snapshot": [True, {"v": offset}],
            },
            compatibility={"min_cli_version": "0.1.0", "unknown_client": ["keep"]},
            generator_version="1.0.1",
        )
        version.id = 200 + offset
        unit.session.flush()
        artifact = unit.skill_artifacts.create(
            skill_version_id=version.id,
            relative_path=f"{slug}/1.0.1.zip",
            size=len(validation.normalized_zip),
            sha256=validation.sha256,
        )
        artifact.id = 300 + offset
        unit.session.flush()
        if status != "draft":
            unit.skill_versions.publish(skill, version)
        skill.status, version.status = status, status
        if status == "archived":
            skill.visibility = "private"
        record_audit(unit, skill.id, slug, offset)
        unit.commit()
    path = artifacts / slug / "1.0.1.zip"
    path.parent.mkdir(parents=True)
    path.write_bytes(validation.normalized_zip)


def record_audit(unit: UnitOfWork, skill_id: int, slug: str, offset: int) -> None:
    """保留旧审计记录和未识别字段。"""
    unit.skill_audit.record(
        skill_id=skill_id,
        skill_slug=slug,
        version="1.0.1",
        action="legacy-fixture",
        actor="old-maintainer",
        outcome="success",
        details={"unknown_audit": offset},
    )


def snapshot(database: Database) -> dict[str, list]:
    """逐表/逐列原值比较，包括 IDs、日期、原 JSON 字面值及历史凭证。"""
    with sqlite3.connect(database.path) as connection:
        return {
            name: sorted(
                connection.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr
            )
            for name in OLD_TABLES
        }


def make_old_fixture(directory: Path) -> tuple:
    """返回升级前完整快照和原制品；来源是合成数据，不是用户数据库。"""
    database = Database(directory / "old-1.0.1.db")
    old_schema(database)
    source = directory / "legacy.json"
    payload = legacy_payload()
    payload["security"]["api_keys"][0]["key"] = "fixture-v101-admin-not-real"
    source.write_text(json.dumps(payload), encoding="utf-8")
    migrate_legacy_config(database, source, directory / "backups")
    artifacts = directory / "artifacts"
    for offset, status in enumerate(
        ("published", "draft", "deprecated", "archived"), 1
    ):
        seed_skill(database, artifacts, offset, status)
    index = build_registry_index(database, base_url=BASE_URL)
    bundle = build_latest_bundle(database, base_url=BASE_URL, artifact_root=artifacts)
    files = {
        path.relative_to(artifacts).as_posix(): path.read_bytes()
        for path in artifacts.rglob("*.zip")
    }
    return database, artifacts, payload, snapshot(database), index, bundle, files
