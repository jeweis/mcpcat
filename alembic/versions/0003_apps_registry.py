"""增量创建 Apps 管理表，旧业务表和制品保持不变。"""

import sqlalchemy as sa

from alembic import op

revision = "0003_apps_registry"
down_revision = "0002_skills_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """仅新增空表，缺省私有草稿由业务模型负责。"""
    _create_apps()
    _create_versions()
    _create_artifacts()
    _create_bindings()


def _create_apps() -> None:
    op.create_table(
        "apps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_ref_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("visibility", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('uploaded', 'upstream')", name="ck_apps_source"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'deprecated', 'archived')",
            name="ck_apps_status",
        ),
        sa.CheckConstraint(
            "visibility IN ('private', 'public')", name="ck_apps_visibility"
        ),
    )


def _create_versions() -> None:
    op.create_table(
        "app_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "app_id",
            sa.Integer(),
            sa.ForeignKey("apps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("content_format", sa.String(32), nullable=False),
        sa.Column("entrypoint", sa.Text(), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("csp_json", sa.JSON(), nullable=False),
        sa.Column("permissions_json", sa.JSON(), nullable=False),
        sa.Column("renderer_version", sa.String(64)),
        sa.Column("changelog", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("app_id", "version", name="uq_app_version"),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'deprecated', 'archived')",
            name="ck_app_versions_status",
        ),
        sa.CheckConstraint(
            "content_format IN ('prefab', 'html', 'upstream')",
            name="ck_app_versions_format",
        ),
    )
    op.create_index("ix_app_versions_app_id", "app_versions", ["app_id"])


def _create_artifacts() -> None:
    op.create_table(
        "app_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "app_version_id",
            sa.Integer(),
            sa.ForeignKey("app_versions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("relative_path", sa.Text(), nullable=False, unique=True),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("integrity_status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "integrity_status IN ('ok', 'missing', 'corrupt')",
            name="ck_app_artifacts_integrity",
        ),
    )


def _create_bindings() -> None:
    op.create_table(
        "app_tool_bindings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "app_version_id",
            sa.Integer(),
            sa.ForeignKey("app_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.String(255), nullable=False),
        sa.Column("server_name", sa.String(64), nullable=False),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("schema_sha256", sa.String(64)),
        sa.Column("visibility_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("app_version_id", "alias", name="uq_app_tool_alias"),
    )
    op.create_index(
        "ix_app_tool_bindings_app_version_id", "app_tool_bindings", ["app_version_id"]
    )


def downgrade() -> None:
    """仅用于明确批准的数据回退；完整恢复仍依赖匹配备份。"""
    for table in ("app_tool_bindings", "app_artifacts", "app_versions", "apps"):
        op.drop_table(table)
