"""Skill 内容变体：保留旧 local artifact 的唯一约束。"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.models import Base, utc_now


class RuntimeIdentityRecord(Base):
    """与配置导入/覆盖隔离的稳定实例和端点身份。"""

    __tablename__ = "runtime_identities"
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(String(36), unique=True)


class SkillRemoteArtifactRecord(Base):
    """需要不同内容时才创建的 remote 制品。"""

    __tablename__ = "skill_remote_artifacts"
    __table_args__ = (
        CheckConstraint(
            "integrity_status IN ('ok', 'missing', 'corrupt')",
            name="ck_remote_artifacts_integrity",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    relative_path: Mapped[str] = mapped_column(Text, unique=True)
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    media_type: Mapped[str] = mapped_column(String(255), default="application/zip")
    integrity_status: Mapped[str] = mapped_column(String(32), default="ok")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class SkillContentVariantRecord(Base):
    """明确 profile/binding 的元数据，可引用同一份 local 字节。"""

    __tablename__ = "skill_content_variants"
    __table_args__ = (
        UniqueConstraint(
            "skill_version_id", "profile", "binding", name="uq_skill_variant_binding"
        ),
        CheckConstraint(
            "profile IN ('local', 'remote')", name="ck_skill_variant_profile"
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'deprecated', 'archived')",
            name="ck_skill_variant_status",
        ),
        CheckConstraint(
            "artifact_id IS NULL OR remote_artifact_id IS NULL",
            name="ck_skill_variant_one_artifact",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    skill_version_id: Mapped[int] = mapped_column(
        ForeignKey("skill_versions.id", ondelete="CASCADE"), index=True
    )
    profile: Mapped[str] = mapped_column(String(16))
    binding: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), default="draft")
    artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("skill_artifacts.id", ondelete="RESTRICT")
    )
    remote_artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("skill_remote_artifacts.id", ondelete="RESTRICT")
    )
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    frontmatter_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
