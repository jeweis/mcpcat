"""Skill 制品原子写入、清理与完整性诊断。"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from filelock import FileLock

from app.core.config import settings
from app.storage.database import Database, resolve_storage_path
from app.storage.skill_repositories import SkillDomainError
from app.storage.unit_of_work import UnitOfWork


@dataclass(frozen=True)
class StoredArtifact:
    """完成登记的制品信息。"""

    path: Path
    relative_path: str
    size: int
    sha256: str


def _artifact_root(root: Optional[Union[str, Path]] = None) -> Path:
    path = resolve_storage_path(root or settings.mcpcat_artifacts_path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_temporary(target_dir: Path, content: bytes) -> tuple[Path, str]:
    temporary = target_dir / f".{uuid.uuid4().hex}.tmp"
    digest = hashlib.sha256()
    try:
        with temporary.open("xb") as output:
            output.write(content)
            digest.update(content)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o600)
        return temporary, digest.hexdigest()
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def store_version_artifact(
    database: Database,
    *,
    skill_version_id: int,
    content: bytes,
    artifact_root: Optional[Union[str, Path]] = None,
) -> StoredArtifact:
    """在事务外落盘，在短事务内登记；失败时清理新制品。"""

    with UnitOfWork(database) as unit:
        version = unit.skill_versions.get_by_id(skill_version_id)
        if version is None:
            raise SkillDomainError("Skill 版本不存在")
        if version.status != "draft":
            raise SkillDomainError("已发布或历史版本的制品不可替换")
        slug = version.skill.slug
        semantic_version = version.version

    root = _artifact_root(artifact_root)
    target_dir = root / slug
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{semantic_version}.zip"
    relative = target.relative_to(root).as_posix()
    lock = FileLock(str(target.with_suffix(".zip.lock")))
    with lock:
        if target.exists():
            raise SkillDomainError("该版本制品已经存在")
        temporary, digest = _write_temporary(target_dir, content)
        try:
            os.replace(temporary, target)
            target.chmod(0o600)
            try:
                with UnitOfWork(database) as unit:
                    unit.skill_artifacts.create(
                        skill_version_id=skill_version_id,
                        relative_path=relative,
                        size=len(content),
                        sha256=digest,
                    )
                    unit.commit()
            except Exception:
                target.unlink(missing_ok=True)
                raise
        finally:
            temporary.unlink(missing_ok=True)
    return StoredArtifact(target, relative, len(content), digest)


def replace_draft_artifact(
    database: Database,
    *,
    skill_version_id: int,
    content: bytes,
    artifact_root: Optional[Union[str, Path]] = None,
) -> StoredArtifact:
    """原子替换草稿制品；数据库失败时恢复旧文件。"""

    with UnitOfWork(database) as unit:
        version = unit.skill_versions.get_by_id(skill_version_id)
        if version is None or version.status != "draft":
            raise SkillDomainError("只有草稿版本可以替换制品")
        artifact = unit.skill_artifacts.get_for_version(skill_version_id)
        if artifact is None:
            raise SkillDomainError("草稿制品不存在")
        relative = artifact.relative_path

    root = _artifact_root(artifact_root)
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(target.with_suffix(".zip.lock")))
    with lock:
        if not target.is_file():
            raise SkillDomainError("草稿制品文件缺失")
        temporary, digest = _write_temporary(target.parent, content)
        backup = target.with_name(f".{target.name}.{uuid.uuid4().hex}.bak")
        try:
            os.replace(target, backup)
            os.replace(temporary, target)
            target.chmod(0o600)
            try:
                with UnitOfWork(database) as unit:
                    artifact = unit.skill_artifacts.get_for_version(skill_version_id)
                    if artifact is None:
                        raise SkillDomainError("草稿制品记录缺失")
                    unit.skill_artifacts.replace_draft(
                        artifact, size=len(content), sha256=digest
                    )
                    unit.commit()
            except Exception:
                target.unlink(missing_ok=True)
                os.replace(backup, target)
                raise
            backup.unlink(missing_ok=True)
        finally:
            temporary.unlink(missing_ok=True)
            if backup.exists() and not target.exists():
                os.replace(backup, target)
    return StoredArtifact(target, relative, len(content), digest)


def diagnose_artifacts(
    database: Database,
    artifact_root: Optional[Union[str, Path]] = None,
) -> dict:
    """核对数据库记录、磁盘文件和哈希，并标记损坏状态。"""

    root = _artifact_root(artifact_root)
    problems = []
    registered = set()
    with UnitOfWork(database) as unit:
        for artifact in unit.skill_artifacts.list_all():
            registered.add(artifact.relative_path)
            path = root / artifact.relative_path
            if not path.is_file():
                unit.skill_artifacts.set_integrity(artifact, "missing")
                problems.append(
                    {"relative_path": artifact.relative_path, "status": "missing"}
                )
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if path.stat().st_size != artifact.size or digest != artifact.sha256:
                unit.skill_artifacts.set_integrity(artifact, "corrupt")
                problems.append(
                    {"relative_path": artifact.relative_path, "status": "corrupt"}
                )
            else:
                unit.skill_artifacts.set_integrity(artifact, "ok")
        unit.commit()

    for path in root.rglob("*.zip"):
        relative = path.relative_to(root).as_posix()
        if relative not in registered:
            problems.append({"relative_path": relative, "status": "orphan"})
    temporary = [path.relative_to(root).as_posix() for path in root.rglob(".*.tmp")]
    return {
        "ok": not problems and not temporary,
        "problems": problems,
        "temporary": temporary,
    }


def cleanup_temporary_artifacts(
    artifact_root: Optional[Union[str, Path]] = None,
    *,
    older_than_seconds: int = 3600,
) -> list[Path]:
    """删除超过阈值的遗留临时制品。"""

    root = _artifact_root(artifact_root)
    cutoff = time.time() - older_than_seconds
    removed = []
    for path in root.rglob(".*.tmp"):
        if path.stat().st_mtime <= cutoff:
            path.unlink(missing_ok=True)
            removed.append(path)
    return removed


def mark_mcp_source_missing(database: Database, server_name: str) -> int:
    """保留历史 Skill，并标记被删除或停用的 MCP 来源缺失。"""

    changed = 0
    with UnitOfWork(database) as unit:
        for skill in unit.skills.list_visible(include_drafts=True):
            source_ref = skill.source_ref_json or {}
            if (
                skill.source_type == "mcp-generated"
                and source_ref.get("mcp_server") == server_name
            ):
                unit.skills.mark_source_missing(skill)
                changed += 1
        unit.commit()
    return changed
