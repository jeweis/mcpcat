"""以 v1.0.1 的原始两份 DDL 构造有历史状态/凭证的隔离迁移夹具。"""

import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

from sqlalchemy import MetaData, Table
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
OLD_API_KEY_COLUMNS = (
    "id",
    "key",
    "name",
    "permission",
    "enabled",
    "created_at",
    "expires_at",
    "feishu_union_id",
    "feishu_open_id",
    "avatar_url",
    "source",
    "present_fields_json",
    "extra_json",
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
                connection.execute(
                    "SELECT "
                    + (
                        ", ".join(f'"{column}"' for column in OLD_API_KEY_COLUMNS)
                        if name == "api_keys"
                        else "*"
                    )
                    + f' FROM "{name}"'
                ).fetchall(),
                key=repr,
            )
            for name in OLD_TABLES
        }


def seed_old_config(database: Database, source: Path, payload: dict) -> None:
    """只向历史列写入夹具，不用要求最新 Schema 的运行时 Key Repository。"""
    old_keys = Table("api_keys", MetaData(), autoload_with=database.engine)
    known = set(OLD_API_KEY_COLUMNS) - {"id", "present_fields_json", "extra_json"}
    security = dict(payload["security"])
    keys = security.pop("api_keys")
    with UnitOfWork(database) as unit:
        for name, value in payload["mcpServers"].items():
            unit.mcp_servers.upsert(name, value)
        for item in keys:
            values = {name: value for name, value in item.items() if name in known}
            values.setdefault("source", "manual")
            values.setdefault("enabled", True)
            unit.session.execute(
                old_keys.insert().values(
                    **values,
                    present_fields_json=sorted(known.intersection(item)),
                    extra_json={
                        name: value for name, value in item.items() if name not in known
                    },
                )
            )
        sections = {
            name: value
            for name, value in payload.items()
            if name not in {"mcpServers", "security"}
        }
        sections["security"] = security
        for name, value in sections.items():
            unit.settings.set(name, value)
        history = unit.migrations.start(
            "legacy_json_import",
            str(source),
            hashlib.sha256(source.read_bytes()).hexdigest(),
        )
        unit.migrations.complete(
            history,
            {
                "mcp_servers": len(payload["mcpServers"]),
                "api_keys": len(keys),
                "settings": len(sections),
            },
        )
        unit.commit()


def make_old_fixture(directory: Path) -> tuple:
    """返回升级前完整快照和原制品；来源是合成数据，不是用户数据库。"""
    database = Database(directory / "old-1.0.1.db")
    old_schema(database)
    source = directory / "legacy.json"
    payload = legacy_payload()
    payload["security"]["api_keys"][0]["key"] = "fixture-v101-admin-not-real"
    source.write_text(json.dumps(payload), encoding="utf-8")
    seed_old_config(database, source, payload)
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
