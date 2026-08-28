"""SQLite ORM 模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    """返回带 UTC 时区的当前时间。"""

    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""


class MCPServerRecord(Base):
    """保存一个 MCP 服务及其完整动态配置。"""

    __tablename__ = "mcp_servers"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class APIKeyRecord(Base):
    """保存现有 API Key 身份和兼容扩展字段。"""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    permission: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    feishu_union_id: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True
    )
    feishu_open_id: Mapped[Optional[str]] = mapped_column(String(255))
    avatar_url: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    present_fields_json: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    extra_json: Mapped[Dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )


class SystemSettingRecord(Base):
    """按顶层配置段保存灵活 JSON 设置。"""

    __tablename__ = "system_settings"

    section: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class MigrationHistoryRecord(Base):
    """记录数据迁移的来源、状态、计数与错误。"""

    __tablename__ = "migration_history"
    __table_args__ = (
        UniqueConstraint(
            "migration_type", "source_sha256", name="uq_migration_type_source_hash"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    migration_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_path: Mapped[Optional[str]] = mapped_column(Text)
    source_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    counters_json: Mapped[Dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class SkillRecord(Base):
    """Registry 中稳定的 Skill 身份。"""

    __tablename__ = "skills"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('mcp-generated', 'uploaded')",
            name="ck_skills_source_type",
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'deprecated', 'archived')",
            name="ck_skills_status",
        ),
        CheckConstraint(
            "visibility IN ('private', 'published')",
            name="ck_skills_visibility",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref_json: Mapped[Dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    visibility: Mapped[str] = mapped_column(
        String(32), nullable=False, default="private"
    )
    latest_version: Mapped[Optional[str]] = mapped_column(String(64))
    latest_published_version: Mapped[Optional[str]] = mapped_column(String(64))
    created_by: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    versions: Mapped[List["SkillVersionRecord"]] = relationship(
        back_populates="skill", cascade="all, delete-orphan"
    )


class SkillVersionRecord(Base):
    """不可变发布语义版本及其来源元数据。"""

    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint("skill_id", "version", name="uq_skill_version"),
        CheckConstraint(
            "status IN ('draft', 'published', 'deprecated', 'archived')",
            name="ck_skill_versions_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    changelog: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_snapshot_json: Mapped[Dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    compatibility_json: Mapped[Dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    tool_schema_hash: Mapped[Optional[str]] = mapped_column(String(64))
    generator_version: Mapped[Optional[str]] = mapped_column(String(64))
    created_by: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    skill: Mapped[SkillRecord] = relationship(back_populates="versions")
    artifact: Mapped[Optional["SkillArtifactRecord"]] = relationship(
        back_populates="skill_version", cascade="all, delete-orphan", uselist=False
    )


class SkillArtifactRecord(Base):
    """一个 Skill 版本对应的 ZIP 制品。"""

    __tablename__ = "skill_artifacts"
    __table_args__ = (
        CheckConstraint(
            "integrity_status IN ('ok', 'missing', 'corrupt')",
            name="ck_skill_artifacts_integrity_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_version_id: Mapped[int] = mapped_column(
        ForeignKey("skill_versions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    relative_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    integrity_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ok"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    skill_version: Mapped[SkillVersionRecord] = relationship(back_populates="artifact")


class SkillBundleRecord(Base):
    """明确版本集合的可复现 bundle 快照。"""

    __tablename__ = "skill_bundles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    manifest_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class SkillAuditEventRecord(Base):
    """Skill 管理操作审计事件。"""

    __tablename__ = "skill_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("skills.id", ondelete="SET NULL"), nullable=True, index=True
    )
    skill_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[Optional[str]] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[Optional[str]] = mapped_column(String(255))
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    details_json: Mapped[Dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
