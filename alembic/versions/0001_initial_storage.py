"""创建 mcpcat 核心持久化表。"""

from typing import Optional, Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial_storage"
down_revision: Optional[str] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=512), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("permission", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("feishu_union_id", sa.String(length=255), nullable=True),
        sa.Column("feishu_open_id", sa.String(length=255), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("present_fields_json", sa.JSON(), nullable=False),
        sa.Column("extra_json", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feishu_union_id"),
        sa.UniqueConstraint("key"),
    )
    op.create_table(
        "mcp_servers",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_index("ix_mcp_servers_type", "mcp_servers", ["type"])
    op.create_table(
        "system_settings",
        sa.Column("section", sa.String(length=128), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("section"),
    )
    op.create_table(
        "migration_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("migration_type", sa.String(length=64), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column("source_sha256", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("counters_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "migration_type",
            "source_sha256",
            name="uq_migration_type_source_hash",
        ),
    )
    op.create_index("ix_migration_history_status", "migration_history", ["status"])


def downgrade() -> None:
    op.drop_index("ix_migration_history_status", table_name="migration_history")
    op.drop_table("migration_history")
    op.drop_table("system_settings")
    op.drop_index("ix_mcp_servers_type", table_name="mcp_servers")
    op.drop_table("mcp_servers")
    op.drop_table("api_keys")
