"""SQLite 基础设施、Repository 与迁移测试。"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.config import settings
from app.storage.database import Database
from app.storage.maintenance import backup_database, check_integrity, get_storage_status
from app.storage.migrations import get_schema_status, upgrade_schema
from app.storage.unit_of_work import UnitOfWork


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Database:
    monkeypatch.setattr(settings, "mcpcat_backups_path", str(tmp_path / "backups"))
    value = Database(tmp_path / "mcpcat.db", busy_timeout_ms=250)
    upgrade_schema(value)
    yield value
    value.dispose()


def test_schema_upgrade_creates_expected_tables(database: Database) -> None:
    status = get_schema_status(database)
    assert status.current == status.head == "0002_skills_registry"
    with database.engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'table'")
            )
        }
    assert {
        "alembic_version",
        "api_keys",
        "mcp_servers",
        "migration_history",
        "system_settings",
    } <= tables


def test_sqlite_connection_pragmas_are_enabled(database: Database) -> None:
    status = get_storage_status(database)
    assert status["foreign_keys"] is True
    assert status["journal_mode"] == "wal"
    assert status["busy_timeout_ms"] == 250
    assert status["integrity"] == "ok"


def test_repositories_round_trip_unknown_json_fields(database: Database) -> None:
    server = {
        "type": "streamable-http",
        "enabled": True,
        "url": "https://example.test/mcp",
        "future_option": {"nested": [1, 2, 3]},
    }
    api_key = {
        "key": "secret-key",
        "name": "Admin",
        "permission": "admin",
        "enabled": True,
        "future_identity": {"provider": "custom"},
    }
    with UnitOfWork(database) as unit:
        unit.mcp_servers.upsert("example", server)
        unit.api_keys.add(api_key)
        unit.settings.set("future_section", {"enabled": True, "unknown": "kept"})
        unit.commit()

    with UnitOfWork(database) as unit:
        assert unit.mcp_servers.get_config("example") == server
        assert unit.api_keys.get_by_key("secret-key")["future_identity"] == {
            "provider": "custom"
        }
        assert unit.settings.get("future_section") == {
            "enabled": True,
            "unknown": "kept",
        }


def test_unit_of_work_rolls_back_on_exception(database: Database) -> None:
    with pytest.raises(RuntimeError, match="stop"):
        with UnitOfWork(database) as unit:
            unit.settings.set("temporary", {"value": 1})
            raise RuntimeError("stop")

    with UnitOfWork(database) as unit:
        assert unit.settings.get("temporary") is None


def test_api_key_uniqueness_failure_can_be_rolled_back(database: Database) -> None:
    payload = {
        "key": "duplicate",
        "name": "Reader",
        "permission": "read",
    }
    with UnitOfWork(database) as unit:
        unit.api_keys.add(payload)
        unit.commit()

    with pytest.raises(IntegrityError):
        with UnitOfWork(database) as unit:
            unit.api_keys.add(payload)
            unit.commit()

    with UnitOfWork(database) as unit:
        assert len(unit.api_keys.list_all()) == 1


def test_busy_timeout_waits_for_locked_writer(database: Database) -> None:
    competing = Database(database.path, busy_timeout_ms=100)
    first = sqlite3.connect(database.path, timeout=0)
    try:
        first.execute("BEGIN IMMEDIATE")
        first.execute(
            "INSERT INTO system_settings(section, value_json, updated_at) "
            "VALUES (?, ?, ?)",
            ("locked", '"value"', "2026-01-01 00:00:00"),
        )
        started = time.monotonic()
        with pytest.raises(OperationalError, match="database is locked"):
            with competing.session_scope() as session:
                session.execute(
                    text(
                        "INSERT INTO system_settings"
                        "(section, value_json, updated_at) "
                        "VALUES ('blocked', '\"value\"', CURRENT_TIMESTAMP)"
                    )
                )
        assert time.monotonic() - started >= 0.08
    finally:
        first.rollback()
        first.close()
        competing.dispose()


def test_backup_is_consistent_and_private(database: Database, tmp_path: Path) -> None:
    with UnitOfWork(database) as unit:
        unit.settings.set("backup_test", {"ready": True})
        unit.commit()

    backup = backup_database(database.path, tmp_path / "manual-backups")
    assert backup is not None
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM system_settings WHERE section = 'backup_test'"
            ).fetchone()[0]
            == 1
        )
    assert check_integrity(database) == "ok"


def test_default_paths_share_existing_mcpcat_volume() -> None:
    paths = {
        settings.mcpcat_config_path,
        settings.mcpcat_database_path,
        settings.mcpcat_artifacts_path,
        settings.mcpcat_backups_path,
    }
    assert all(Path(value).parts[0] == ".mcpcat" for value in paths)
