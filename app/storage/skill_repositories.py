"""Skills Registry Repository 与领域约束。"""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage.models import (
    SkillArtifactRecord,
    SkillAuditEventRecord,
    SkillBundleRecord,
    SkillRecord,
    SkillVersionRecord,
    utc_now,
)

SKILL_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


class SkillDomainError(ValueError):
    """Skill 领域约束被违反。"""


def validate_skill_slug(slug: str) -> str:
    """校验 Agent Skills 兼容 slug。"""

    if len(slug) > 64 or not SKILL_SLUG_PATTERN.fullmatch(slug):
        raise SkillDomainError(
            "Skill slug 必须为不超过 64 字符的小写字母数字连字符格式"
        )
    return slug


def validate_semver(version: str) -> str:
    """校验完整 SemVer 2.0.0 字符串。"""

    match = SEMVER_PATTERN.fullmatch(version)
    if match is None:
        raise SkillDomainError("Skill 版本必须是合法 SemVer")
    prerelease = match.group(4)
    if prerelease:
        for identifier in prerelease.split("."):
            if (
                identifier.isdigit()
                and len(identifier) > 1
                and identifier.startswith("0")
            ):
                raise SkillDomainError("SemVer 预发布数字标识不得包含前导零")
    return version


def semver_key(version: str) -> tuple:
    """生成满足 SemVer 优先级的排序键。"""

    match = SEMVER_PATTERN.fullmatch(validate_semver(version))
    prerelease = match.group(4) if match else None
    identifiers = []
    if prerelease:
        for value in prerelease.split("."):
            identifiers.append((0, int(value)) if value.isdigit() else (1, value))
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        1 if prerelease is None else 0,
        tuple(identifiers),
    )


class SkillRepository:
    """Skill 身份和可见性操作。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        slug: str,
        display_name: str,
        description: str,
        source_type: str,
        source_ref: Optional[Dict[str, Any]] = None,
        created_by: Optional[str] = None,
    ) -> SkillRecord:
        validate_skill_slug(slug)
        if source_type not in {"mcp-generated", "uploaded"}:
            raise SkillDomainError("不支持的 Skill 来源")
        row = SkillRecord(
            slug=slug,
            display_name=display_name,
            description=description,
            source_type=source_type,
            source_ref_json=deepcopy(source_ref or {}),
            status="draft",
            visibility="private",
            created_by=created_by,
        )
        self.session.add(row)
        return row

    def get_by_slug(self, slug: str) -> Optional[SkillRecord]:
        return self.session.scalar(select(SkillRecord).where(SkillRecord.slug == slug))

    def list_visible(self, *, include_drafts: bool = False) -> list[SkillRecord]:
        statement = select(SkillRecord).order_by(SkillRecord.slug)
        if not include_drafts:
            statement = statement.where(
                SkillRecord.visibility == "published",
                SkillRecord.status.in_({"published", "deprecated"}),
            )
        return list(self.session.scalars(statement).all())

    def delete_draft(self, skill: SkillRecord) -> None:
        if any(version.status != "draft" for version in skill.versions):
            raise SkillDomainError("包含已发布历史的 Skill 不可物理删除")
        self.session.delete(skill)

    @staticmethod
    def update_metadata(
        skill: SkillRecord,
        *,
        display_name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        if display_name is not None:
            skill.display_name = display_name
        if description is not None:
            skill.description = description
        skill.updated_at = utc_now()

    @staticmethod
    def mark_source_missing(skill: SkillRecord) -> None:
        source_ref = deepcopy(skill.source_ref_json or {})
        source_ref["source_status"] = "missing"
        skill.source_ref_json = source_ref
        if skill.status == "published":
            skill.status = "deprecated"
        skill.updated_at = utc_now()


class SkillVersionRepository:
    """Skill 版本与不可变发布状态操作。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        skill: SkillRecord,
        version: str,
        changelog: str = "",
        source_snapshot: Optional[Dict[str, Any]] = None,
        compatibility: Optional[Dict[str, Any]] = None,
        tool_schema_hash: Optional[str] = None,
        generator_version: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> SkillVersionRecord:
        validate_semver(version)
        row = SkillVersionRecord(
            skill=skill,
            version=version,
            status="draft",
            changelog=changelog,
            source_snapshot_json=deepcopy(source_snapshot or {}),
            compatibility_json=deepcopy(compatibility or {}),
            tool_schema_hash=tool_schema_hash,
            generator_version=generator_version,
            created_by=created_by,
        )
        if skill.latest_version is None or semver_key(version) > semver_key(
            skill.latest_version
        ):
            skill.latest_version = version
        skill.updated_at = utc_now()
        self.session.add(row)
        return row

    def get(self, skill_id: int, version: str) -> Optional[SkillVersionRecord]:
        return self.session.scalar(
            select(SkillVersionRecord).where(
                SkillVersionRecord.skill_id == skill_id,
                SkillVersionRecord.version == version,
            )
        )

    def get_by_id(self, version_id: int) -> Optional[SkillVersionRecord]:
        return self.session.get(SkillVersionRecord, version_id)

    def list_for_skill(self, skill_id: int) -> list[SkillVersionRecord]:
        rows = list(
            self.session.scalars(
                select(SkillVersionRecord).where(
                    SkillVersionRecord.skill_id == skill_id
                )
            ).all()
        )
        return sorted(rows, key=lambda row: semver_key(row.version), reverse=True)

    def delete_draft(self, version: SkillVersionRecord) -> None:
        if version.status != "draft":
            raise SkillDomainError("非草稿版本不可物理删除")
        self.session.delete(version)

    @staticmethod
    def update_draft(
        version: SkillVersionRecord,
        *,
        changelog: Optional[str] = None,
        compatibility: Optional[Dict[str, Any]] = None,
    ) -> None:
        if version.status != "draft":
            raise SkillDomainError("已发布或历史版本不可原地修改")
        if changelog is not None:
            version.changelog = changelog
        if compatibility is not None:
            version.compatibility_json = deepcopy(compatibility)

    @staticmethod
    def publish(skill: SkillRecord, version: SkillVersionRecord) -> None:
        if version.status != "draft":
            raise SkillDomainError("只有草稿版本可以发布")
        if version.artifact is None:
            raise SkillDomainError("版本没有可发布制品")
        version.status = "published"
        version.published_at = utc_now()
        skill.status = "published"
        skill.visibility = "published"
        if skill.latest_published_version is None or semver_key(
            version.version
        ) > semver_key(skill.latest_published_version):
            skill.latest_published_version = version.version
        skill.updated_at = utc_now()

    @staticmethod
    def deprecate(
        skill: SkillRecord, version: Optional[SkillVersionRecord] = None
    ) -> None:
        if version is not None:
            version.status = "deprecated"
        skill.status = "deprecated"
        skill.updated_at = utc_now()

    @staticmethod
    def archive(skill: SkillRecord) -> None:
        skill.status = "archived"
        skill.visibility = "private"
        skill.updated_at = utc_now()


class SkillArtifactRepository:
    """ZIP 制品元数据操作。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        skill_version_id: int,
        relative_path: str,
        size: int,
        sha256: str,
        media_type: str = "application/zip",
    ) -> SkillArtifactRecord:
        path = PurePosixPath(relative_path)
        if path.is_absolute() or ".." in path.parts:
            raise SkillDomainError("制品相对路径无效")
        row = SkillArtifactRecord(
            skill_version_id=skill_version_id,
            relative_path=path.as_posix(),
            size=size,
            media_type=media_type,
            sha256=sha256,
            integrity_status="ok",
        )
        self.session.add(row)
        return row

    def get_for_version(self, skill_version_id: int) -> Optional[SkillArtifactRecord]:
        return self.session.scalar(
            select(SkillArtifactRecord).where(
                SkillArtifactRecord.skill_version_id == skill_version_id
            )
        )

    def list_all(self) -> list[SkillArtifactRecord]:
        return list(self.session.scalars(select(SkillArtifactRecord)).all())

    @staticmethod
    def set_integrity(artifact: SkillArtifactRecord, status: str) -> None:
        if status not in {"ok", "missing", "corrupt"}:
            raise SkillDomainError("制品完整性状态无效")
        artifact.integrity_status = status

    @staticmethod
    def replace_draft(
        artifact: SkillArtifactRecord,
        *,
        size: int,
        sha256: str,
    ) -> None:
        if artifact.skill_version.status != "draft":
            raise SkillDomainError("已发布或历史版本的制品不可替换")
        artifact.size = size
        artifact.sha256 = sha256
        artifact.integrity_status = "ok"


class SkillBundleRepository:
    """集合包快照操作。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        snapshot_key: str,
        manifest: Dict[str, Any],
        sha256: str,
        relative_path: str,
    ) -> SkillBundleRecord:
        row = SkillBundleRecord(
            snapshot_key=snapshot_key,
            manifest_json=deepcopy(manifest),
            sha256=sha256,
            relative_path=relative_path,
        )
        self.session.add(row)
        return row

    def get_by_sha256(self, sha256: str) -> Optional[SkillBundleRecord]:
        return self.session.scalar(
            select(SkillBundleRecord).where(SkillBundleRecord.sha256 == sha256)
        )

    def get_by_snapshot_key(self, snapshot_key: str) -> Optional[SkillBundleRecord]:
        return self.session.scalar(
            select(SkillBundleRecord).where(
                SkillBundleRecord.snapshot_key == snapshot_key
            )
        )

    def get_by_id(self, bundle_id: int) -> Optional[SkillBundleRecord]:
        return self.session.get(SkillBundleRecord, bundle_id)


class SkillAuditRepository:
    """Skill 管理操作的安全审计记录。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        *,
        skill_slug: str,
        action: str,
        outcome: str,
        skill_id: Optional[int] = None,
        version: Optional[str] = None,
        actor: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> SkillAuditEventRecord:
        row = SkillAuditEventRecord(
            skill_id=skill_id,
            skill_slug=skill_slug,
            version=version,
            action=action,
            actor=actor,
            outcome=outcome,
            details_json=deepcopy(details or {}),
        )
        self.session.add(row)
        return row

    def list_for_skill(self, skill_slug: str) -> list[SkillAuditEventRecord]:
        return list(
            self.session.scalars(
                select(SkillAuditEventRecord)
                .where(SkillAuditEventRecord.skill_slug == skill_slug)
                .order_by(SkillAuditEventRecord.id)
            ).all()
        )
