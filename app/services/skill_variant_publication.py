"""冻结整个版本的变体集合，local/remote latest 分开推进。"""

from pathlib import Path

from sqlalchemy import text

from app.services.skill_content_locks import version_content_lock
from app.services.skill_variant_artifacts import (
    inspect_variant_zip,
    read_checked_zip,
    variant_artifact,
)
from app.storage.database import Database
from app.storage.models import utc_now
from app.storage.skill_repositories import SkillDomainError, semver_key
from app.storage.unit_of_work import UnitOfWork


def _publish_content_version(
    database: Database,
    *,
    slug: str,
    version: str,
    actor: str | None = None,
    artifact_root: Path | None = None,
    remote_public: bool = False,
) -> None:
    """明确审核整批内容；remote-only 不改变旧 local 指针。"""
    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug(slug)
        row = unit.skill_versions.get(skill.id, version) if skill else None
        if row is None:
            raise SkillDomainError("Skill 版本不存在")
        version_id = row.id
    with version_content_lock(database, version_id):
        _publish_locked(database, slug, version, actor, artifact_root, remote_public)


def publish_content_version(
    database: Database,
    *,
    slug: str,
    version: str,
    actor: str | None = None,
    artifact_root: Path | None = None,
    remote_public: bool = False,
) -> None:
    """发布与失败都保留安全审计，不记录 ZIP 正文、参数或凭证。"""
    try:
        _publish_content_version(
            database,
            slug=slug,
            version=version,
            actor=actor,
            artifact_root=artifact_root,
            remote_public=remote_public,
        )
    except Exception as error:
        try:
            with UnitOfWork(database) as unit:
                skill = unit.skills.get_by_slug(slug)
                unit.skill_audit.record(
                    skill_id=skill.id if skill else None,
                    skill_slug=slug,
                    version=version,
                    action="publish",
                    actor=actor,
                    outcome="failed",
                    details={"error_type": type(error).__name__},
                )
                unit.commit()
        except Exception:
            pass
        raise


def _publish_locked(
    database: Database,
    slug: str,
    version: str,
    actor: str | None,
    artifact_root: Path | None,
    remote_public: bool,
) -> None:
    with UnitOfWork(database) as unit:
        unit.session.execute(text("BEGIN IMMEDIATE"))
        skill = unit.skills.get_by_slug(slug)
        row = unit.skill_versions.get(skill.id, version)
        if row.status != "draft":
            raise SkillDomainError("只有草稿版本可以发布")
        variants = unit.skill_variants.list_for_version(row.id)
        if not variants:
            raise SkillDomainError("Skill 版本没有完整可发布制品（没有内容变体）")
        expected = (row.source_snapshot_json or {}).get("content_profiles") or [
            item.profile for item in variants
        ]
        if set(expected) != {variant.profile for variant in variants}:
            raise SkillDomainError("版本内容批次尚未完成，不能提前发布缺失模式")
        for variant in variants:
            artifact = variant_artifact(unit, variant)
            content = read_checked_zip(artifact, artifact_root=artifact_root)
            frontmatter, manifest = inspect_variant_zip(
                content, remote=variant.profile == "remote"
            )
            if frontmatter["name"] != slug:
                raise SkillDomainError("变体 name 与 Skill 不一致")
            if variant.frontmatter_json and frontmatter != variant.frontmatter_json:
                raise SkillDomainError("变体 frontmatter 与实际文件不一致")
            if variant.manifest_json.get("files") and manifest != variant.manifest_json:
                raise SkillDomainError("变体清单与实际文件不一致")
            variant.frontmatter_json, variant.manifest_json = frontmatter, manifest
            variant.status = "published"
            if variant.profile == "remote":
                variant.metadata_json = {
                    **variant.metadata_json,
                    "public_remote": remote_public,
                }
        _advance_pointers(skill, row, any(item.profile == "local" for item in variants))
        unit.skill_audit.record(
            skill_id=skill.id,
            skill_slug=slug,
            version=version,
            action="publish",
            actor=actor,
            outcome="success",
            details={"profiles": sorted({item.profile for item in variants})},
        )
        unit.commit()


def _advance_pointers(skill, version, has_local: bool) -> None:
    version.status = "published"
    version.published_at = utc_now()
    skill.status, skill.visibility = "published", "published"
    if has_local and (
        skill.latest_published_version is None
        or semver_key(version.version) > semver_key(skill.latest_published_version)
    ):
        skill.latest_published_version = version.version
    skill.updated_at = utc_now()


def withdraw_remote_variant(
    database: Database,
    *,
    slug: str,
    version: str,
    binding: str = "",
    actor: str | None = None,
) -> None:
    """撤销远程分发而不重写字节或改变旧 local 下载/固定版本契约。"""
    with UnitOfWork(database) as unit:
        unit.session.execute(text("BEGIN IMMEDIATE"))
        skill = unit.skills.get_by_slug(slug)
        row = unit.skill_versions.get(skill.id, version) if skill else None
        variant = unit.skill_variants.get(row.id, "remote", binding) if row else None
        if variant is None or variant.status not in {"published", "deprecated"}:
            raise SkillDomainError("远程变体不存在或没有已发布内容")
        variant.status = "archived"
        unit.skill_audit.record(
            skill_id=skill.id,
            skill_slug=slug,
            version=version,
            action="withdraw-remote",
            actor=actor,
            outcome="success",
            details={"binding": binding},
        )
        unit.commit()
