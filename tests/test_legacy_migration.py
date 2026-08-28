"""旧 JSON 迁移、恢复和兼容配置 facade 测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from filelock import FileLock

from app.core.config import settings
from app.services.config_service import ConfigService
from app.storage.database import Database
from app.storage.legacy_migration import (
    LegacyMigrationError,
    export_legacy_config,
    migrate_legacy_config,
)
from app.storage.migrations import upgrade_schema
from app.storage.unit_of_work import UnitOfWork


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Database:
    monkeypatch.setattr(settings, "mcpcat_backups_path", str(tmp_path / "backups"))
    value = Database(tmp_path / "mcpcat.db", busy_timeout_ms=100)
    upgrade_schema(value)
    yield value
    ConfigService.reset_database()
    value.dispose()


def legacy_payload() -> dict:
    return {
        "mcpServers": {
            "remote": {
                "type": "streamable-http",
                "url": "https://upstream.example/mcp",
                "headers": {"X-Custom-Auth": "oauth-secret"},
                "oauth": {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "token": {"access_token": "access-secret"},
                },
                "future_transport_option": {"value": 1},
            }
        },
        "security": {
            "auth_header_name": "X-Mcpcat-Key",
            "future_security": True,
            "api_keys": [
                {
                    "key": "existing-api-key",
                    "name": "Existing",
                    "permission": "write",
                    "enabled": True,
                    "feishu_union_id": "union-id",
                    "future_identity": "kept",
                }
            ],
        },
        "app": {"secret_key": "encrypted-data-key", "custom_app": 42},
        "feishu": {"app_id": "app-id", "app_secret": "encrypted-secret"},
        "catalog": {"enabled": True},
        "unknown_top_level": {"nested": ["kept"]},
    }


def write_json(path: Path, value: dict) -> bytes:
    raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return raw


def test_migrates_secrets_unknown_fields_and_archives_source(
    database: Database, tmp_path: Path
) -> None:
    source = tmp_path / "config.json"
    raw = write_json(source, legacy_payload())

    result = migrate_legacy_config(database, source, tmp_path / "backups")

    assert result.state == "migrated"
    assert result.backup_path is not None and result.backup_path.read_bytes() == raw
    assert result.archive_path is not None and result.archive_path.read_bytes() == raw
    assert not source.exists()
    ConfigService.configure_database(database)
    assert ConfigService.load_raw_config() == legacy_payload()
    with UnitOfWork(database) as unit:
        history = unit.migrations.find("legacy_json_import", result.source_sha256)
        assert history is not None
        assert history.status == "completed"
        assert history.counters_json == {
            "mcp_servers": 1,
            "api_keys": 1,
            "settings": 5,
        }


@pytest.mark.parametrize("raw", [b"{broken", b"[]", b'{"mcpServers": []}'])
def test_invalid_legacy_file_blocks_without_modifying_source(
    database: Database, tmp_path: Path, raw: bytes
) -> None:
    source = tmp_path / "config.json"
    source.write_bytes(raw)

    with pytest.raises(LegacyMigrationError):
        migrate_legacy_config(database, source, tmp_path / "backups")

    assert source.read_bytes() == raw
    with UnitOfWork(database) as unit:
        assert unit.mcp_servers.list_configs() == {}
        assert unit.api_keys.list_all() == []
        assert unit.settings.list_all() == {}


def test_existing_database_wins_over_legacy_file(
    database: Database, tmp_path: Path
) -> None:
    with UnitOfWork(database) as unit:
        unit.settings.set("app", {"from": "database"})
        unit.commit()
    source = tmp_path / "config.json"
    write_json(source, legacy_payload())

    result = migrate_legacy_config(database, source, tmp_path / "backups")

    assert result.state == "skipped_existing_database"
    assert source.exists()
    with UnitOfWork(database) as unit:
        assert unit.settings.get("app") == {"from": "database"}


def test_retries_cleanly_after_pre_commit_interruption(
    database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.storage import legacy_migration

    source = tmp_path / "config.json"
    write_json(source, legacy_payload())
    original_import = legacy_migration._import_config

    def interrupted(unit, config):
        original_import(unit, config)
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(legacy_migration, "_import_config", interrupted)
    with pytest.raises(RuntimeError, match="simulated"):
        migrate_legacy_config(database, source, tmp_path / "backups")
    with UnitOfWork(database) as unit:
        assert unit.mcp_servers.list_configs() == {}

    monkeypatch.setattr(legacy_migration, "_import_config", original_import)
    result = migrate_legacy_config(database, source, tmp_path / "backups")
    assert result.state == "migrated"


def test_recovers_archive_after_post_commit_interruption(
    database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.storage import legacy_migration

    source = tmp_path / "config.json"
    write_json(source, legacy_payload())
    original_archive = legacy_migration._archive_source

    def interrupted(*_args, **_kwargs):
        raise RuntimeError("archive interruption")

    monkeypatch.setattr(legacy_migration, "_archive_source", interrupted)
    with pytest.raises(RuntimeError, match="archive interruption"):
        migrate_legacy_config(database, source, tmp_path / "backups")
    assert source.exists()

    monkeypatch.setattr(legacy_migration, "_archive_source", original_archive)
    result = migrate_legacy_config(database, source, tmp_path / "backups")
    assert result.state == "archive_recovered"
    assert not source.exists()


def test_file_lock_prevents_concurrent_migration(
    database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "config.json"
    write_json(source, legacy_payload())
    monkeypatch.setattr(settings, "mcpcat_legacy_migration_lock_timeout_sec", 0.01)
    lock_path = database.path.with_suffix(database.path.suffix + ".migration.lock")
    with FileLock(str(lock_path)):
        with pytest.raises(LegacyMigrationError, match="迁移锁超时"):
            migrate_legacy_config(database, source, tmp_path / "backups")


def test_config_facade_diff_write_and_export_without_overwrite(
    database: Database, tmp_path: Path
) -> None:
    source = tmp_path / "config.json"
    write_json(source, legacy_payload())
    migrate_legacy_config(database, source, tmp_path / "backups")
    ConfigService.configure_database(database)
    updated = ConfigService.load_raw_config()
    updated["mcpServers"]["remote"]["note"] = "updated"
    updated["unknown_top_level"]["new"] = True

    assert ConfigService.save_config(updated) is True
    assert ConfigService.load_raw_config() == updated

    target = tmp_path / "export.json"
    export_legacy_config(database, target)
    assert json.loads(target.read_text("utf-8")) == updated
    with pytest.raises(FileExistsError):
        export_legacy_config(database, target)
