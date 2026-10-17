"""Skills 变体登记、共享/独立制品和管理层内容读取。"""

import io
import os
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text

from app.core.config import settings
from app.services.skill_content_locks import version_content_lock
from app.services.skill_variant_artifacts import (
    inspect_variant_zip,
    read_checked_zip,
    variant_artifact,
)
from app.storage.database import Database, resolve_storage_path
from app.storage.models import SkillArtifactRecord, SkillVersionRecord
from app.storage.skill_repositories import SkillDomainError, semver_key
from app.storage.skill_variant_models import (
    SkillContentVariantRecord,
    SkillRemoteArtifactRecord,
)
from app.storage.unit_of_work import UnitOfWork


def attach_local_variant(unit: UnitOfWork, version_id: int, content: bytes) -> None:
    """登记/刷新草稿 local 元数据，旧发布版本不在此修改。"""
    version = unit.skill_versions.get_by_id(version_id)
    if version is None or version.status != "draft":
        raise SkillDomainError("只有草稿可以登记 local 变体")
    artifact = unit.skill_artifacts.get_for_version(version_id)
    if artifact is None:
        raise SkillDomainError("local 制品缺失")
    # 底层制品仓储也用于完整性诊断夹具；格式校验属于上传/生成边界。
    # 非 ZIP 不注册协议内容，但不改变原底层存储契约。
    if not zipfile.is_zipfile(io.BytesIO(content)):
        return
    frontmatter, manifest = inspect_variant_zip(content)
    row = unit.skill_variants.get(version_id, "local")
    if row is None:
        row = unit.skill_variants.create(
            version_id=version_id, profile="local", artifact_id=artifact.id
        )
    row.artifact_id = artifact.id
    row.remote_artifact_id = None
    row.frontmatter_json, row.manifest_json = frontmatter, manifest
    for remote in unit.skill_variants.list_for_version(version_id):
        if remote.profile == "remote" and remote.artifact_id == artifact.id:
            inspect_variant_zip(content, remote=True)
            remote.frontmatter_json, remote.manifest_json = frontmatter, manifest


def _store_remote_variant(
    database: Database,
    *,
    version_id: int,
    content: bytes,
    binding: str = "",
    metadata: dict | None = None,
    artifact_root: Path | None = None,
) -> int:
    """只增加草稿变体；相同不可变内容优先共享 local 引用。"""
    frontmatter, manifest = inspect_variant_zip(content, remote=True)
    root = resolve_storage_path(artifact_root or settings.mcpcat_artifacts_path)
    created_path = None
    try:
        with UnitOfWork(database) as unit:
            unit.session.execute(text("BEGIN IMMEDIATE"))
            version = unit.skill_versions.get_by_id(version_id)
            if version is None or frontmatter["name"] != version.skill.slug:
                raise SkillDomainError("remote 内容与目标 Skill 不一致")
            row = _new_remote_reference(unit, version, content, binding, root)
            if row.artifact_id is None:
                shared = _shared_frozen_remote(unit, version.skill_id, content, root)
                if shared is not None:
                    row.remote_artifact_id = shared.id
            if row.artifact_id is None and row.remote_artifact_id is None:
                relative = f"_remote/{version_id}/{uuid4().hex}.zip"
                created_path = _write_remote(root / relative, content)
                artifact = unit.skill_variants.create_remote_artifact(
                    relative_path=relative, size=len(content), sha256=_sha256(content)
                )
                row.remote_artifact_id = artifact.id
            row.frontmatter_json, row.manifest_json = frontmatter, manifest
            row.metadata_json = deepcopy(metadata or {})
            unit.commit()
            return row.id
    except Exception:
        if created_path is not None:
            created_path.unlink(missing_ok=True)
        raise


def store_remote_variant(
    database: Database,
    *,
    version_id: int,
    content: bytes,
    binding: str = "",
    metadata: dict | None = None,
    artifact_root: Path | None = None,
) -> int:
    """与 local 编辑及发布串行化，不能改变冻结变体集合。"""
    with version_content_lock(database, version_id):
        return _store_remote_variant(
            database,
            version_id=version_id,
            content=content,
            binding=binding,
            metadata=metadata,
            artifact_root=artifact_root,
        )


def _sha256(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()


def _shared_frozen_remote(unit, skill_id: int, content: bytes, root: Path):
    candidates = unit.session.scalars(
        select(SkillRemoteArtifactRecord)
        .join(
            SkillContentVariantRecord,
            SkillContentVariantRecord.remote_artifact_id
            == SkillRemoteArtifactRecord.id,
        )
        .join(
            SkillVersionRecord,
            SkillVersionRecord.id == SkillContentVariantRecord.skill_version_id,
        )
        .where(
            SkillVersionRecord.skill_id == skill_id,
            SkillVersionRecord.status.in_({"published", "deprecated"}),
            SkillRemoteArtifactRecord.sha256 == _sha256(content),
            SkillRemoteArtifactRecord.integrity_status == "ok",
        )
    ).all()
    for candidate in candidates:
        try:
            if read_checked_zip(candidate, artifact_root=root) == content:
                return candidate
        except SkillDomainError:
            continue
    return None


def _write_remote(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        path.chmod(0o600)
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _new_remote_reference(
    unit: UnitOfWork, version, content: bytes, binding: str, root: Path
):
    matching = unit.session.scalars(
        select(SkillArtifactRecord).where(
            SkillArtifactRecord.sha256 == _sha256(content),
        )
    ).all()
    matching.sort(key=lambda artifact: artifact.skill_version_id != version.id)
    candidates = (
        artifact
        for artifact in matching
        if artifact.skill_version.skill_id == version.skill_id
        and (
            artifact.skill_version_id == version.id
            or artifact.skill_version.status != "draft"
        )
        and artifact.integrity_status == "ok"
    )
    share = None
    for artifact in candidates:
        try:
            if read_checked_zip(artifact, artifact_root=root) == content:
                share = artifact
                break
        except SkillDomainError:
            continue
    return unit.skill_variants.create(
        version_id=version.id,
        profile="remote",
        binding=binding,
        artifact_id=share.id if share else None,
    )


def clone_uploaded_for_remote(
    database: Database,
    *,
    slug: str,
    source_version: str,
    new_version: str,
    binding: str = "",
    actor: str | None = None,
    artifact_root: Path | None = None,
    metadata: dict | None = None,
) -> int:
    """历史上传内容适配 remote 时创建新版本，并引用原字节而不复制 ZIP。"""
    with UnitOfWork(database) as unit:
        unit.session.execute(text("BEGIN IMMEDIATE"))
        skill = unit.skills.get_by_slug(slug)
        if not skill or skill.source_type != "uploaded":
            raise SkillDomainError("MCP 生成内容须使用 remote 模板重新生成")
        old = unit.skill_versions.get(skill.id, source_version)
        if old is None or old.status not in {"published", "deprecated"}:
            raise SkillDomainError("必须选择已冻结上传版本")
        artifact = unit.skill_artifacts.get_for_version(old.id)
        content = (
            read_checked_zip(artifact, artifact_root=artifact_root)
            if artifact
            else None
        )
        if content is None:
            raise SkillDomainError("旧版本没有可共享 local 制品")
        frontmatter, manifest = inspect_variant_zip(content, remote=True)
        if unit.skill_versions.get(skill.id, new_version):
            raise SkillDomainError("目标版本已存在")
        if semver_key(new_version) <= semver_key(
            skill.latest_version or source_version
        ):
            raise SkillDomainError("remote 适配必须创建更高版本")
        snapshot = {
            **deepcopy(old.source_snapshot_json),
            "content_profiles": ["local", "remote"],
        }
        row = unit.skill_versions.create(
            skill=skill, version=new_version, source_snapshot=snapshot, created_by=actor
        )
        unit.session.flush()
        for profile, target in (("local", ""), ("remote", binding)):
            variant = unit.skill_variants.create(
                version_id=row.id,
                profile=profile,
                binding=target,
                artifact_id=artifact.id,
                frontmatter=frontmatter,
                manifest=manifest,
            )
            if profile == "remote":
                variant.metadata_json = deepcopy(metadata or {})
        unit.commit()
        return row.id


def load_variant(
    database: Database,
    *,
    slug: str,
    version: str,
    profile: str,
    binding: str = "",
    include_drafts: bool = False,
    artifact_root: Path | None = None,
) -> tuple[dict[str, Any], bytes]:
    """明确版本读取，草稿仅供管理层；公共读取仍由协议层校验身份/端点。"""
    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug(slug)
        row = unit.skill_versions.get(skill.id, version) if skill else None
        if skill is None or row is None or profile not in {"local", "remote"}:
            raise SkillDomainError("Skill 版本/模式不存在")
        variant = unit.skill_variants.get(row.id, profile, binding)
        if variant is None:
            raise SkillDomainError("该版本缺少请求的内容变体")
        if not include_drafts and (
            skill.visibility != "published"
            or skill.status == "archived"
            or row.status not in {"published", "deprecated"}
            or variant.status not in {"published", "deprecated"}
        ):
            raise SkillDomainError("变体尚未发布或已撤销")
        artifact = variant_artifact(unit, variant)
        content = read_checked_zip(artifact, artifact_root=artifact_root)
        frontmatter, manifest = inspect_variant_zip(content, remote=profile == "remote")
        if variant.frontmatter_json and frontmatter != variant.frontmatter_json:
            raise SkillDomainError("变体 frontmatter 与原文不一致")
        if variant.manifest_json.get("files") and manifest != variant.manifest_json:
            raise SkillDomainError("变体清单与原始字节不一致")
        return {
            "id": variant.id,
            "version_id": row.id,
            "skill_id": skill.id,
            "slug": slug,
            "version": version,
            "profile": profile,
            "binding": binding,
            "status": variant.status,
            "frontmatter": frontmatter,
            "manifest": manifest,
            "sha256": artifact.sha256,
            "metadata": variant.metadata_json,
        }, content


def latest_variant(
    database: Database, *, slug: str, profile: str, binding: str = ""
) -> str | None:
    """latest 按 profile/binding 独立选择，不改变旧 local latest。"""
    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug(slug)
        if (
            skill is None
            or skill.visibility != "published"
            or skill.status == "archived"
        ):
            return None
        for version in unit.skill_versions.list_for_skill(skill.id):
            variant = unit.skill_variants.get(version.id, profile, binding)
            if (
                version.status in {"published", "deprecated"}
                and variant
                and variant.status in {"published", "deprecated"}
            ):
                try:
                    load_variant(
                        database,
                        slug=slug,
                        version=version.version,
                        profile=profile,
                        binding=binding,
                    )
                except SkillDomainError:
                    continue
                return version.version
    return None
