"""Apps 的独立业务模型，不复用或改变旧 Skills 表。"""

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


class AppRecord(Base):
    """稳定 App 身份与接入来源。"""

    __tablename__ = "apps"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('uploaded', 'upstream')", name="ck_apps_source"
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'deprecated', 'archived')",
            name="ck_apps_status",
        ),
        CheckConstraint(
            "visibility IN ('private', 'public')", name="ck_apps_visibility"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str] = mapped_column(String(32), default="uploaded")
    source_ref_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    visibility: Mapped[str] = mapped_column(String(32), default="private")
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AppVersionRecord(Base):
    """版本内容与经审核的 UI/权限配置。"""

    __tablename__ = "app_versions"
    __table_args__ = (
        UniqueConstraint("app_id", "version", name="uq_app_version"),
        CheckConstraint(
            "status IN ('draft', 'published', 'deprecated', 'archived')",
            name="ck_app_versions_status",
        ),
        CheckConstraint(
            "content_format IN ('prefab', 'html', 'upstream')",
            name="ck_app_versions_format",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("apps.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="draft")
    content_format: Mapped[str] = mapped_column(String(32))
    entrypoint: Mapped[str] = mapped_column(Text)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    csp_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    permissions_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    renderer_version: Mapped[str | None] = mapped_column(String(64))
    rendered_sha256: Mapped[str | None] = mapped_column(String(64))
    rendering_version: Mapped[str | None] = mapped_column(String(64))
    rendered_payload_sha256: Mapped[str | None] = mapped_column(String(64))
    changelog: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppArtifactRecord(Base):
    """上传原始制品与其完整性元数据。"""

    __tablename__ = "app_artifacts"
    __table_args__ = (
        CheckConstraint(
            "integrity_status IN ('ok', 'missing', 'corrupt')",
            name="ck_app_artifacts_integrity",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_version_id: Mapped[int] = mapped_column(
        ForeignKey("app_versions.id", ondelete="CASCADE"), unique=True
    )
    relative_path: Mapped[str] = mapped_column(Text, unique=True)
    media_type: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    integrity_status: Mapped[str] = mapped_column(String(32), default="ok")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class AppToolBindingRecord(Base):
    """明确版本内的 UI 工具别名及来源，不保存用户凭证。"""

    __tablename__ = "app_tool_bindings"
    __table_args__ = (
        UniqueConstraint("app_version_id", "alias", name="uq_app_tool_alias"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_version_id: Mapped[int] = mapped_column(
        ForeignKey("app_versions.id", ondelete="CASCADE"), index=True
    )
    alias: Mapped[str] = mapped_column(String(255))
    server_name: Mapped[str] = mapped_column(String(64))
    tool_name: Mapped[str] = mapped_column(String(255))
    schema_sha256: Mapped[str | None] = mapped_column(String(64))
    visibility_json: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["app"])
