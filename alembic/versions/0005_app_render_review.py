"""保存 App 派生入口审核摘要，不重写历史包或旧 Skills。"""

import sqlalchemy as sa

from alembic import op

revision = "0005_app_render_review"
down_revision = "0004_skill_content_variants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """可空列保留已有 Apps 数据；旧版本不自动重新发布。"""
    op.add_column(
        "app_versions", sa.Column("rendered_sha256", sa.String(64), nullable=True)
    )
    op.add_column(
        "app_versions", sa.Column("rendering_version", sa.String(64), nullable=True)
    )
    op.add_column(
        "app_versions",
        sa.Column("rendered_payload_sha256", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    """仅移除新增摘要列，原始制品与其他业务记录不变。"""
    op.drop_column("app_versions", "rendering_version")
    op.drop_column("app_versions", "rendered_payload_sha256")
    op.drop_column("app_versions", "rendered_sha256")
