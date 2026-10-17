"""新管理接口上传 local/remote/both；旧上传默认契约不变。"""

from pathlib import Path

from sqlalchemy import select, text

from app.core.config import settings
from app.services.skill_artifact_cleanup import discard_unreferenced_remote_artifacts
from app.services.skill_artifact_service import store_version_artifact
from app.services.skill_content_locks import version_content_lock
from app.services.skill_content_service import store_remote_variant
from app.services.skill_package_validator import validate_skill_zip
from app.services.skill_variant_artifacts import inspect_variant_zip
from app.storage.database import Database, resolve_storage_path
from app.storage.models import SkillArtifactRecord
from app.storage.skill_repositories import SkillDomainError, validate_semver
from app.storage.unit_of_work import UnitOfWork


def upload_skill_profiles(
    database: Database,
    *,
    content: bytes,
    version: str,
    selection: str = "local",
    changelog: str = "",
    binding: str = "",
    metadata: dict | None = None,
    actor: str | None = None,
    artifact_root: Path | None = None,
) -> tuple[str, int]:
    """创建明确预期模式的整批草稿，缺失模式时禁止提前发布。"""
    if selection not in {"local", "remote", "both"}:
        raise SkillDomainError("不支持的生成/上传模式")
    validation = validate_skill_zip(content)
    validate_semver(version)
    profiles = ["local", "remote"] if selection == "both" else [selection]
    if "remote" in profiles:
        inspect_variant_zip(validation.normalized_zip, remote=True)
    version_id = _create_upload_draft(
        database, validation, version, profiles, actor, changelog
    )
    with version_content_lock(database, version_id):
        return _store_upload_profiles(
            database,
            validation,
            version_id,
            profiles,
            binding,
            metadata,
            actor,
            artifact_root,
        )


def _store_upload_profiles(
    database, validation, version_id, profiles, binding, metadata, actor, artifact_root
) -> tuple[str, int]:
    try:
        if "local" in profiles:
            store_version_artifact(
                database,
                skill_version_id=version_id,
                content=validation.normalized_zip,
                artifact_root=artifact_root,
            )
        if "remote" in profiles:
            store_remote_variant(
                database,
                version_id=version_id,
                content=validation.normalized_zip,
                binding=binding,
                metadata=metadata,
                artifact_root=artifact_root,
            )
        _record_upload(database, validation.name, version_id, actor, profiles)
        return validation.name, version_id
    except Exception as error:
        _discard_incomplete_draft(database, version_id, artifact_root=artifact_root)
        with UnitOfWork(database) as unit:
            unit.skill_audit.record(
                skill_slug=validation.name,
                action="upload-variants",
                actor=actor,
                outcome="failed",
                details={"error_type": type(error).__name__},
            )
            unit.commit()
        raise


def _record_upload(database, slug, version_id, actor, profiles) -> None:
    with UnitOfWork(database) as unit:
        row = unit.skill_versions.get_by_id(version_id)
        unit.skill_audit.record(
            skill_id=row.skill_id,
            skill_slug=slug,
            version=row.version,
            action="upload-variants",
            actor=actor,
            outcome="success",
            details={"profiles": profiles},
        )
        unit.commit()


def _create_upload_draft(
    database, validation, version, profiles, actor, changelog
) -> int:
    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug(validation.name)
        if skill is None:
            skill = unit.skills.create(
                slug=validation.name,
                display_name=validation.name,
                description=validation.description,
                source_type="uploaded",
                created_by=actor,
            )
        elif skill.source_type != "uploaded":
            raise SkillDomainError("同名 Skill 已由其他来源占用")
        if unit.skill_versions.get(skill.id, version):
            raise SkillDomainError("Skill 版本已存在")
        row = unit.skill_versions.create(
            skill=skill,
            version=version,
            changelog=changelog,
            created_by=actor,
            source_snapshot={
                "frontmatter": validation.metadata,
                "content_profiles": profiles,
                "files": [entry.__dict__ for entry in validation.files],
                "scripts": validation.scripts,
            },
            compatibility=(
                {"agent_skills": validation.compatibility}
                if validation.compatibility
                else {}
            ),
        )
        unit.session.flush()
        unit.commit()
        return row.id


def _discard_incomplete_draft(
    database: Database, version_id: int, *, artifact_root: Path | None = None
) -> None:
    paths = []
    with UnitOfWork(database) as unit:
        unit.session.execute(text("BEGIN IMMEDIATE"))
        row = unit.skill_versions.get_by_id(version_id)
        if row is not None and row.status == "draft":
            skill = row.skill
            paths.extend(
                artifact.relative_path
                for artifact in unit.session.scalars(
                    select(SkillArtifactRecord).where(
                        SkillArtifactRecord.skill_version_id == version_id
                    )
                )
            )
            remote_ids = {
                item.remote_artifact_id
                for item in unit.skill_variants.list_for_version(version_id)
                if item.remote_artifact_id is not None
            }
            unit.skill_versions.delete_draft(row)
            unit.session.flush()
            paths.extend(discard_unreferenced_remote_artifacts(unit, remote_ids))
            if not unit.skill_versions.list_for_skill(skill.id):
                unit.skills.delete_draft(skill)
            unit.commit()
    root = resolve_storage_path(
        artifact_root or settings.mcpcat_artifacts_path
    ).resolve()
    for relative in paths:
        path = (root / relative).resolve()
        if path.is_relative_to(root):
            path.unlink(missing_ok=True)
