"""企业微信账号身份与一次性登录事务。"""

import sqlalchemy as sa

from alembic import op

revision = "0006_wecom_login"
down_revision = "0005_app_render_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加可空身份字段，保持既有 Key 与飞书账号不变。"""
    with op.batch_alter_table("api_keys") as batch:
        batch.add_column(sa.Column("wecom_corp_id", sa.String(255), nullable=True))
        batch.add_column(sa.Column("wecom_user_id", sa.String(255), nullable=True))
        batch.create_unique_constraint(
            "uq_api_keys_wecom", ["wecom_corp_id", "wecom_user_id"]
        )
    op.create_table(
        "wecom_login_transactions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("browser_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.Float, nullable=False, index=True),
        sa.Column("payload_json", sa.JSON, nullable=False),
    )


def downgrade() -> None:
    """移除企微专用字段和临时登录事务。"""
    op.drop_table("wecom_login_transactions")
    with op.batch_alter_table("api_keys") as batch:
        batch.drop_constraint("uq_api_keys_wecom", type_="unique")
        batch.drop_column("wecom_user_id")
        batch.drop_column("wecom_corp_id")
