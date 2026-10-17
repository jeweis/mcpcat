"""Skill 上传、草稿与生命周期应用服务。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from sqlalchemy import text

from app.core.config import settings
from app.services.skill_artifact_cleanup import discard_unreferenced_remote_artifacts
from app.services.skill_artifact_service import (
    replace_draft_artifact,
    store_version_artifact,
)
from app.services.skill_package_validator import (
    SkillPackageValidation,
    validate_skill_zip,
)
from app.storage.database import Database, resolve_storage_path
from app.storage.skill_repositories import SkillDomainError, validate_semver
from app.storage.unit_of_work import UnitOfWork


def _record_failure(
    database: Database,
    *,
    slug: str,
    action: str,
    actor: Optional[str],
    version: Optional[str],
    error: Exception,
) -> None:
    try:
        with UnitOfWork(database) as unit:
            skill = unit.skills.get_by_slug(slug)
            unit.skill_audit.record(
                skill_id=skill.id if skill else None,
                skill_slug=slug,
                version=version,
                action=action,
                actor=actor,
                outcome="failed",
                details={"error_type": type(error).__name__},
            )
            unit.commit()
    except Exception:
        pass


def upload_skill(
    database: Database,
    *,
    content: bytes,
    version: str,
    changelog: str,
    actor: Optional[str],
    artifact_root: Optional[Union[str, Path]] = None,
) -> tuple[int, SkillPackageValidation]:
    """校验并创建 uploaded Skill 草稿版本，不执行包内任何代码。"""

    validation = validate_skill_zip(content)
    validate_semver(version)
    created_skill = False
    skill_id: Optional[int] = None
    version_id: Optional[int] = None
    try:
        with UnitOfWork(database) as unit:
            skill = unit.skills.get_by_slug(validation.name)
            if skill is None:
                skill = unit.skills.create(
                    slug=validation.name,
                    display_name=validation.name,
                    description=validation.description,
                    source_type="uploaded",
                    source_ref={"upload": True},
                    created_by=actor,
                )
                created_skill = True
            elif skill.source_type != "uploaded":
                raise SkillDomainError("同名 Skill 已由其他来源占用")
            if unit.skill_versions.get(skill.id, version) is not None:
                raise SkillDomainError("该 Skill 版本已存在")
            compatibility = {}
            if validation.compatibility:
                compatibility["agent_skills"] = validation.compatibility
            version_row = unit.skill_versions.create(
                skill=skill,
                version=version,
                changelog=changelog,
                source_snapshot={
                    "frontmatter": validation.metadata,
                    "files": [entry.__dict__ for entry in validation.files],
                    "scripts": validation.scripts,
                },
                compatibility=compatibility,
                created_by=actor,
            )
            unit.session.flush()
            skill_id = skill.id
            version_id = version_row.id
            unit.commit()

        store_version_artifact(
            database,
            skill_version_id=version_id,
            content=validation.normalized_zip,
            artifact_root=artifact_root,
        )
        with UnitOfWork(database) as unit:
            unit.skill_audit.record(
                skill_id=skill_id,
                skill_slug=validation.name,
                version=version,
                action="upload",
                actor=actor,
                outcome="success",
                details={
                    "sha256": validation.sha256,
                    "file_count": len(validation.files),
                    "script_count": len(validation.scripts),
                },
            )
            unit.commit()
        return version_id, validation
    except Exception as error:
        if version_id is not None:
            try:
                with UnitOfWork(database) as unit:
                    version_row = unit.skill_versions.get_by_id(version_id)
                    if version_row is not None and version_row.status == "draft":
                        skill = version_row.skill
                        unit.skill_versions.delete_draft(version_row)
                        unit.session.flush()
                        if created_skill and not skill.versions:
                            unit.skills.delete_draft(skill)
                        unit.commit()
            except Exception:
                pass
        _record_failure(
            database,
            slug=validation.name,
            version=version,
            action="upload",
            actor=actor,
            error=error,
        )
        raise


def publish_skill_version(
    database: Database,
    *,
    slug: str,
    version: str,
    actor: Optional[str],
    artifact_root: Optional[Union[str, Path]] = None,
) -> None:
    """发布带有完整制品的草稿版本。"""

    from app.services.skill_variant_publication import publish_content_version

    publish_content_version(
        database, slug=slug, version=version, actor=actor, artifact_root=artifact_root
    )


def _replace_draft_package(
    database: Database,
    *,
    slug: str,
    version: str,
    content: bytes,
    changelog: Optional[str],
    actor: Optional[str],
    artifact_root: Optional[Union[str, Path]] = None,
) -> SkillPackageValidation:
    """用共享校验器替换管理员编辑后的草稿 ZIP。"""

    validation = validate_skill_zip(content)
    if validation.name != slug:
        raise SkillDomainError("编辑包 name 与目标 Skill 不一致")
    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug(slug)
        if skill is None:
            raise SkillDomainError("Skill 不存在")
        version_row = unit.skill_versions.get(skill.id, version)
        if version_row is None or version_row.status != "draft":
            raise SkillDomainError("只有草稿版本可以编辑")
        version_id = version_row.id
    replace_draft_artifact(
        database,
        skill_version_id=version_id,
        content=validation.normalized_zip,
        artifact_root=artifact_root,
    )
    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug(slug)
        version_row = unit.skill_versions.get(skill.id, version)
        unit.skill_versions.update_draft(version_row, changelog=changelog)
        version_row.source_snapshot_json = {
            **(version_row.source_snapshot_json or {}),
            "frontmatter": validation.metadata,
            "files": [entry.__dict__ for entry in validation.files],
            "scripts": validation.scripts,
        }
        unit.skills.update_metadata(skill, description=validation.description)
        unit.skill_audit.record(
            skill_id=skill.id,
            skill_slug=slug,
            version=version,
            action="edit-draft",
            actor=actor,
            outcome="success",
            details={"sha256": validation.sha256},
        )
        unit.commit()
    return validation


def replace_draft_package(
    database: Database,
    *,
    slug: str,
    version: str,
    content: bytes,
    changelog: Optional[str],
    actor: Optional[str],
    artifact_root: Optional[Union[str, Path]] = None,
) -> SkillPackageValidation:
    """编辑的文件、清单和来源快照整体与发布互斥，不能发布半更新状态。"""
    from app.services.skill_content_locks import version_content_lock

    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug(slug)
        row = unit.skill_versions.get(skill.id, version) if skill else None
        if row is None:
            raise SkillDomainError("Skill 版本不存在")
        version_id = row.id
    with version_content_lock(database, version_id):
        return _replace_draft_package(
            database,
            slug=slug,
            version=version,
            content=content,
            changelog=changelog,
            actor=actor,
            artifact_root=artifact_root,
        )


def change_skill_lifecycle(
    database: Database,
    *,
    slug: str,
    action: str,
    actor: Optional[str],
    version: Optional[str] = None,
) -> None:
    """废弃或归档 Skill，并记录操作者。"""

    if action not in {"deprecate", "archive"}:
        raise SkillDomainError("不支持的生命周期操作")
    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug(slug)
        if skill is None:
            raise SkillDomainError("Skill 不存在")
        version_row = (
            unit.skill_versions.get(skill.id, version) if version is not None else None
        )
        if version is not None and version_row is None:
            raise SkillDomainError("Skill 版本不存在")
        if action == "deprecate":
            unit.skill_versions.deprecate(skill, version_row)
        else:
            unit.skill_versions.archive(skill)
        unit.skill_audit.record(
            skill_id=skill.id,
            skill_slug=slug,
            version=version,
            action=action,
            actor=actor,
            outcome="success",
        )
        unit.commit()


def delete_draft_skill(
    database: Database,
    *,
    slug: str,
    actor: Optional[str],
    artifact_root: Optional[Union[str, Path]] = None,
) -> None:
    """仅物理删除从未发布的 Skill，并清理其制品。"""

    paths: list[str] = []
    with UnitOfWork(database) as unit:
        unit.session.execute(text("BEGIN IMMEDIATE"))
        skill = unit.skills.get_by_slug(slug)
        if skill is None:
            raise SkillDomainError("Skill 不存在")
        remote_ids = set()
        for version in skill.versions:
            remote_ids.update(
                variant.remote_artifact_id
                for variant in unit.skill_variants.list_for_version(version.id)
                if variant.remote_artifact_id is not None
            )
            artifact = unit.skill_artifacts.get_for_version(version.id)
            if artifact is not None:
                paths.append(artifact.relative_path)
        unit.skill_audit.record(
            skill_id=skill.id,
            skill_slug=slug,
            action="delete",
            actor=actor,
            outcome="success",
        )
        unit.skills.delete_draft(skill)
        unit.session.flush()
        paths.extend(discard_unreferenced_remote_artifacts(unit, remote_ids))
        unit.commit()
    root = resolve_storage_path(
        artifact_root or settings.mcpcat_artifacts_path
    ).resolve()
    for relative in paths:
        path = (root / relative).resolve()
        if path.is_relative_to(root):
            path.unlink(missing_ok=True)
