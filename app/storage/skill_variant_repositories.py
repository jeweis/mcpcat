"""变体仓储，不隐式改变旧 local latest 或冻结版本。"""

import re
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage.models import SkillArtifactRecord, SkillVersionRecord
from app.storage.skill_repositories import SkillDomainError
from app.storage.skill_variant_models import (
    SkillContentVariantRecord,
    SkillRemoteArtifactRecord,
)


class SkillVariantRepository:
    """按照明确版本/profile/端点读取和登记变体。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(
        self, version_id: int, profile: str, binding: str = ""
    ) -> SkillContentVariantRecord | None:
        """查询不会在缺失时回退到另一 profile。"""
        return self.session.scalar(
            select(SkillContentVariantRecord).where(
                SkillContentVariantRecord.skill_version_id == version_id,
                SkillContentVariantRecord.profile == profile,
                SkillContentVariantRecord.binding == binding,
            )
        )

    def list_for_version(self, version_id: int) -> list[SkillContentVariantRecord]:
        """返回明确版本所有变体，不读取附件正文。"""
        return list(
            self.session.scalars(
                select(SkillContentVariantRecord)
                .where(
                    SkillContentVariantRecord.skill_version_id == version_id,
                )
                .order_by(
                    SkillContentVariantRecord.profile, SkillContentVariantRecord.binding
                )
            )
        )

    def create_remote_artifact(
        self, *, relative_path: str, size: int, sha256: str
    ) -> SkillRemoteArtifactRecord:
        """登记独立 remote 包；不提供覆盖历史记录的操作。"""
        path = PurePosixPath(relative_path)
        if (
            not relative_path
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in relative_path
            or ":" in relative_path
            or "\x00" in relative_path
        ):
            raise SkillDomainError("remote 制品路径无效")
        if size <= 0 or not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise SkillDomainError("remote 制品完整性元数据无效")
        row = SkillRemoteArtifactRecord(
            relative_path=path.as_posix(), size=size, sha256=sha256
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_remote_artifact(self, artifact_id: int) -> SkillRemoteArtifactRecord | None:
        """返回明确制品身份，不根据 latest 替换。"""
        return self.session.get(SkillRemoteArtifactRecord, artifact_id)

    def create(
        self,
        *,
        version_id: int,
        profile: str,
        binding: str = "",
        artifact_id: int | None = None,
        remote_artifact_id: int | None = None,
        frontmatter: dict[str, Any] | None = None,
        manifest: dict[str, Any] | None = None,
    ) -> SkillContentVariantRecord:
        """只在草稿增加内容，可共享已登记 local artifact。"""
        version = self.session.get(SkillVersionRecord, version_id)
        if not version or version.status != "draft":
            raise SkillDomainError("冻结版本增加变体必须创建新版本")
        if profile not in {"local", "remote"} or (profile == "local" and binding):
            raise SkillDomainError("变体 profile/binding 无效")
        if artifact_id is not None and remote_artifact_id is not None:
            raise SkillDomainError("变体只能引用一份制品")
        if (
            remote_artifact_id is not None
            and self.get_remote_artifact(remote_artifact_id) is None
        ):
            raise SkillDomainError("remote 制品不存在")
        if artifact_id is not None:
            artifact = self.session.get(SkillArtifactRecord, artifact_id)
            if artifact is None:
                raise SkillDomainError("共享制品不存在")
            if (
                artifact.skill_version_id != version_id
                and artifact.skill_version.status == "draft"
            ):
                raise SkillDomainError("不能共享其他仍可变更的草稿制品")
        if self.get(version_id, profile, binding):
            raise SkillDomainError("变体已存在")
        row = SkillContentVariantRecord(
            skill_version_id=version_id,
            profile=profile,
            binding=binding,
            artifact_id=artifact_id,
            remote_artifact_id=remote_artifact_id,
            frontmatter_json=frontmatter or {},
            manifest_json=manifest or {},
        )
        self.session.add(row)
        self.session.flush()
        return row
