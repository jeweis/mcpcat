"""增量登记旧 local 制品并保存稳定端点身份，不改写 ZIP。"""

import uuid

import sqlalchemy as sa

from alembic import op

revision = "0004_skill_content_variants"
down_revision = "0003_apps_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建变体表，事务中回填旧制品及独立 runtime identity 表。"""
    op.create_table(
        "skill_remote_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("relative_path", sa.Text(), nullable=False, unique=True),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("integrity_status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "integrity_status IN ('ok', 'missing', 'corrupt')",
            name="ck_remote_artifacts_integrity",
        ),
    )
    op.create_index(
        "ix_skill_remote_artifacts_sha256", "skill_remote_artifacts", ["sha256"]
    )
    op.create_table(
        "skill_content_variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "skill_version_id",
            sa.Integer(),
            sa.ForeignKey("skill_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("profile", sa.String(16), nullable=False),
        sa.Column("binding", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "artifact_id",
            sa.Integer(),
            sa.ForeignKey("skill_artifacts.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "remote_artifact_id",
            sa.Integer(),
            sa.ForeignKey("skill_remote_artifacts.id", ondelete="RESTRICT"),
        ),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("frontmatter_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "skill_version_id", "profile", "binding", name="uq_skill_variant_binding"
        ),
        sa.CheckConstraint(
            "profile IN ('local', 'remote')", name="ck_skill_variant_profile"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'deprecated', 'archived')",
            name="ck_skill_variant_status",
        ),
        sa.CheckConstraint(
            "artifact_id IS NULL OR remote_artifact_id IS NULL",
            name="ck_skill_variant_one_artifact",
        ),
    )
    op.create_index(
        "ix_skill_content_variants_skill_version_id",
        "skill_content_variants",
        ["skill_version_id"],
    )
    op.create_table(
        "runtime_identities",
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("value", sa.String(36), nullable=False, unique=True),
    )
    _backfill_local()
    _save_identity()


def _backfill_local() -> None:
    connection = op.get_bind()
    metadata = sa.MetaData()
    artifacts = sa.Table("skill_artifacts", metadata, autoload_with=connection)
    versions = sa.Table("skill_versions", metadata, autoload_with=connection)
    variants = sa.Table("skill_content_variants", metadata, autoload_with=connection)
    rows = connection.execute(
        sa.select(
            artifacts.c.id,
            artifacts.c.skill_version_id,
            artifacts.c.created_at,
            versions.c.status,
            versions.c.source_snapshot_json,
        ).join(versions, versions.c.id == artifacts.c.skill_version_id)
    ).mappings()
    for row in rows:
        snapshot = row["source_snapshot_json"] or {}
        connection.execute(
            variants.insert().values(
                skill_version_id=row["skill_version_id"],
                profile="local",
                binding="",
                status=row["status"],
                artifact_id=row["id"],
                frontmatter_json=snapshot.get("frontmatter", {}),
                manifest_json={"files": snapshot.get("files", [])},
                metadata_json={"legacy_import": True},
                created_at=row["created_at"],
            )
        )


def _save_identity() -> None:
    connection = op.get_bind()
    identities = sa.Table("runtime_identities", sa.MetaData(), autoload_with=connection)
    connection.execute(
        identities.insert(),
        [
            {"key": key, "value": str(uuid.uuid4())}
            for key in (
                "instance",
                "endpoint:skills",
                "endpoint:apps",
                "endpoint:code-mode",
            )
        ],
    )


def downgrade() -> None:
    """删除新增变体表；旧 artifact 表不变。"""
    op.drop_table("skill_content_variants")
    op.drop_table("skill_remote_artifacts")
    op.drop_table("runtime_identities")
