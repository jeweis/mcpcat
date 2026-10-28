"""企微升级保留历史手动/飞书账号与稳定 ID。"""

from alembic import command
from sqlalchemy import text

from app.core.config import settings
from app.storage.database import Database
from app.storage.migrations import create_alembic_config, upgrade_schema
from app.storage.unit_of_work import UnitOfWork


def test_upgrade_preserves_existing_keys_and_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "mcpcat_backups_path", str(tmp_path / "backups"))
    database = Database(tmp_path / "old.db")
    command.upgrade(create_alembic_config(database.path), "0005_app_render_review")
    with database.engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO api_keys
              (id, key, name, permission, enabled, source, feishu_union_id,
               present_fields_json, extra_json)
            VALUES
              (7, 'manual-fixture-key', 'Admin', 'write', 1, 'manual', NULL,
               '["key","name","permission","enabled","source"]', '{}'),
              (12, 'feishu-fixture-key', 'Feishu', 'read', 0, 'feishu', 'union-fixture',
               '["key","name","permission","enabled","source","feishu_union_id"]',
               '{"future_identity": "kept"}')
        """))
    assert upgrade_schema(database).current == "0006_wecom_login"
    with UnitOfWork(database) as unit:
        manual = unit.api_keys.get_by_key("manual-fixture-key")
        feishu = unit.api_keys.get_by_feishu_union_id("union-fixture")
        assert manual["permission"] == "write" and manual["enabled"]
        assert feishu["key"] == "feishu-fixture-key" and not feishu["enabled"]
        assert feishu["future_identity"] == "kept"
        assert unit.session.execute(
            text("SELECT id FROM api_keys ORDER BY id")
        ).scalars().all() == [7, 12]
    assert list((tmp_path / "backups").glob("*"))
    database.dispose()
