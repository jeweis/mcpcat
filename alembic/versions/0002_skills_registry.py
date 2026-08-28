"""创建 Skills Registry 数据表。"""

from typing import Optional, Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0002_skills_registry"
down_revision: Optional[str] = "0001_initial_storage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_ref_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("latest_version", sa.String(length=64), nullable=True),
        sa.Column("latest_published_version", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('mcp-generated', 'uploaded')",
            name="ck_skills_source_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'deprecated', 'archived')",
            name="ck_skills_status",
        ),
        sa.CheckConstraint(
            "visibility IN ('private', 'published')",
            name="ck_skills_visibility",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "skill_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("changelog", sa.Text(), nullable=False),
        sa.Column("source_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("compatibility_json", sa.JSON(), nullable=False),
        sa.Column("tool_schema_hash", sa.String(length=64), nullable=True),
        sa.Column("generator_version", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'deprecated', 'archived')",
            name="ck_skill_versions_status",
        ),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("skill_id", "version", name="uq_skill_version"),
    )
    op.create_index("ix_skill_versions_skill_id", "skill_versions", ["skill_id"])
    op.create_table(
        "skill_artifacts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("skill_version_id", sa.Integer(), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("integrity_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "integrity_status IN ('ok', 'missing', 'corrupt')",
            name="ck_skill_artifacts_integrity_status",
        ),
        sa.ForeignKeyConstraint(
            ["skill_version_id"], ["skill_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("relative_path"),
        sa.UniqueConstraint("skill_version_id"),
    )
    op.create_table(
        "skill_bundles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_key", sa.String(length=64), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("relative_path"),
        sa.UniqueConstraint("sha256"),
        sa.UniqueConstraint("snapshot_key"),
    )
    op.create_table(
        "skill_audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=True),
        sa.Column("skill_slug", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_skill_audit_events_skill_id", "skill_audit_events", ["skill_id"]
    )
    op.create_index(
        "ix_skill_audit_events_skill_slug", "skill_audit_events", ["skill_slug"]
    )
    op.create_index("ix_skill_audit_events_action", "skill_audit_events", ["action"])


def downgrade() -> None:
    op.drop_index("ix_skill_audit_events_action", table_name="skill_audit_events")
    op.drop_index("ix_skill_audit_events_skill_slug", table_name="skill_audit_events")
    op.drop_index("ix_skill_audit_events_skill_id", table_name="skill_audit_events")
    op.drop_table("skill_audit_events")
    op.drop_table("skill_bundles")
    op.drop_table("skill_artifacts")
    op.drop_index("ix_skill_versions_skill_id", table_name="skill_versions")
    op.drop_table("skill_versions")
    op.drop_table("skills")
