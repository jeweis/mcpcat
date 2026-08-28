"""现有 MCP、认证、OAuth、飞书和设置的 SQLite 回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import settings
from app.models.mcp_config import PermissionType
from app.services.config_service import ConfigService
from app.services.security_service import SecurityService
from app.storage.database import Database
from app.storage.migrations import upgrade_schema


@pytest.fixture
def configured_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Database, Path]:
    legacy_path = tmp_path / "config.json"
    monkeypatch.setattr(settings, "mcpcat_config_path", str(legacy_path))
    monkeypatch.setattr(settings, "mcpcat_backups_path", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "mcpcat_secret_key", None)
    database = Database(tmp_path / "mcpcat.db")
    upgrade_schema(database)
    ConfigService.configure_database(database)
    assert ConfigService.save_config(ConfigService._create_default_config())
    yield database, legacy_path
    ConfigService.reset_database()
    database.dispose()


def test_mcp_metadata_oauth_and_delete_use_repository(
    configured_database: tuple[Database, Path],
) -> None:
    _database, legacy_path = configured_database
    original = {
        "type": "streamable-http",
        "url": "https://example.test/mcp",
        "unknown_transport": {"kept": True},
    }
    assert ConfigService.add_server_to_config("remote", original)
    assert ConfigService.update_server_meta("remote", note="note", tags=["tag"])
    assert ConfigService.update_server_oauth(
        "remote",
        {
            "client_id": "client",
            "client_secret": "secret",
            "token": {"access_token": "access"},
        },
    )

    persisted = ConfigService.get_server_config("remote")
    assert persisted["unknown_transport"] == {"kept": True}
    assert persisted["note"] == "note"
    assert persisted["oauth"]["client_secret"] == "secret"

    replacement = {**persisted, "url": "https://new.example/mcp"}
    assert ConfigService.update_server_config("remote", replacement)
    assert ConfigService.get_server_config("remote")["url"].startswith("https://new")
    assert ConfigService.remove_server_from_config("remote")
    assert ConfigService.get_server_config("remote") is None
    assert not legacy_path.exists()


def test_api_key_crud_and_default_initialization_use_repository(
    configured_database: tuple[Database, Path],
) -> None:
    _database, legacy_path = configured_database
    security = SecurityService()
    created = security.ensure_default_keys()
    assert len(created) == 2
    assert security.ensure_default_keys() == []

    key = security.add_api_key(
        name="Manual", permission=PermissionType.READ, key="manual-key-123"
    )
    assert security.verify_api_key(key.key).name == "Manual"
    assert security.update_api_key(key.key, permission="write")
    assert security.verify_api_key(key.key).permission == PermissionType.WRITE
    assert security.remove_api_key(key.key)
    assert security.verify_api_key(key.key) is None
    assert not legacy_path.exists()


def test_settings_and_feishu_binding_use_repositories(
    configured_database: tuple[Database, Path],
) -> None:
    _database, legacy_path = configured_database
    security = SecurityService()
    ConfigService.update_setting_section(
        "security", {"auth_header_name": "X-Mcpcat-Key", "future": True}
    )
    ConfigService.update_setting_section("catalog", {"enabled": False})
    app_config = ConfigService.get_setting_section("app")
    app_config["public_base_url"] = "https://mcpcat.example"
    ConfigService.update_setting_section("app", app_config)

    assert security.get_auth_header_name() == "X-Mcpcat-Key"
    assert ConfigService.get_public_base_url() == "https://mcpcat.example"
    assert ConfigService.get_setting_section("catalog") == {"enabled": False}

    feishu = security.update_feishu_settings(
        enabled=True,
        app_id="app-id",
        app_secret="plain-secret",
        default_permission=PermissionType.READ,
    )
    assert feishu.app_secret == "plain-secret"
    stored_feishu = ConfigService.get_setting_section("feishu")
    assert stored_feishu["app_secret"] != "plain-secret"

    account, created = security.find_or_create_feishu_key(
        union_id="union-id",
        open_id="open-id",
        name="User",
        avatar_url="https://example.test/avatar.png",
    )
    assert created is True
    assert account.source == "feishu"
    updated = security.update_feishu_account(
        "union-id", permission=PermissionType.WRITE, enabled=False
    )
    assert updated.permission == PermissionType.WRITE
    assert updated.enabled is False
    assert not legacy_path.exists()
