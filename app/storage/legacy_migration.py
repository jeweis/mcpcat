"""旧 config.json 到 SQLite 的锁定、事务化迁移。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union

from filelock import FileLock, Timeout
from sqlalchemy import func, select

from app.core.config import settings
from app.storage.database import Database, resolve_storage_path
from app.storage.models import APIKeyRecord, MCPServerRecord, SystemSettingRecord
from app.storage.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)
MIGRATION_TYPE = "legacy_json_import"


class LegacyMigrationError(RuntimeError):
    """旧配置无法安全迁移。"""


@dataclass(frozen=True)
class LegacyMigrationResult:
    """迁移判定和执行结果。"""

    state: str
    source_sha256: Optional[str] = None
    backup_path: Optional[Path] = None
    archive_path: Optional[Path] = None


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _exclusive_path(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / name
    suffix = 1
    while candidate.exists():
        candidate = directory / f"{Path(name).stem}-{suffix}{Path(name).suffix}"
        suffix += 1
    return candidate


def _validate_legacy_config(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise LegacyMigrationError("旧配置根节点必须是 JSON 对象")
    servers = value.get("mcpServers", {})
    security = value.get("security", {})
    if not isinstance(servers, dict):
        raise LegacyMigrationError("旧配置 mcpServers 必须是对象")
    if not isinstance(security, dict):
        raise LegacyMigrationError("旧配置 security 必须是对象")
    api_keys = security.get("api_keys", [])
    if not isinstance(api_keys, list):
        raise LegacyMigrationError("旧配置 security.api_keys 必须是数组")
    for name, config in servers.items():
        if not isinstance(name, str) or not isinstance(config, dict):
            raise LegacyMigrationError("旧配置中的 MCP 名称和配置类型无效")
    required_key_fields = {"key", "name", "permission"}
    for item in api_keys:
        if not isinstance(item, dict) or not required_key_fields <= set(item):
            raise LegacyMigrationError("旧配置中存在无效 API Key")
    return deepcopy(value)


def _database_has_business_data(database: Database) -> bool:
    with database.engine.connect() as connection:
        counts = (
            connection.scalar(select(func.count()).select_from(MCPServerRecord)),
            connection.scalar(select(func.count()).select_from(APIKeyRecord)),
            connection.scalar(select(func.count()).select_from(SystemSettingRecord)),
        )
    return any(int(value or 0) > 0 for value in counts)


def _rebuild_config(unit: UnitOfWork) -> Dict[str, Any]:
    config = unit.settings.list_all()
    security = deepcopy(config.get("security") or {})
    security["api_keys"] = unit.api_keys.list_all()
    config["security"] = security
    config["mcpServers"] = unit.mcp_servers.list_configs()
    return config


def _normalize_for_comparison(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized = deepcopy(config)
    normalized.setdefault("mcpServers", {})
    security = normalized.setdefault("security", {})
    api_keys = security.setdefault("api_keys", [])
    for item in api_keys:
        item.setdefault("enabled", True)
        item.setdefault("source", "manual")
        for field in (
            "created_at",
            "expires_at",
            "feishu_union_id",
            "feishu_open_id",
            "avatar_url",
        ):
            if item.get(field) is None:
                item.pop(field, None)
    return normalized


def _import_config(unit: UnitOfWork, config: Dict[str, Any]) -> Dict[str, int]:
    servers = config.get("mcpServers", {})
    security = deepcopy(config.get("security") or {})
    api_keys = security.pop("api_keys", [])
    for name, value in servers.items():
        unit.mcp_servers.upsert(name, value)
    unit.api_keys.replace_all(api_keys)
    settings_payload = {
        key: value
        for key, value in config.items()
        if key not in {"mcpServers", "security"}
    }
    settings_payload["security"] = security
    for section, value in settings_payload.items():
        unit.settings.set(section, value)
    return {
        "mcp_servers": len(servers),
        "api_keys": len(api_keys),
        "settings": len(settings_payload),
    }


def _backup_source(source: Path, backup_dir: Path, digest: str) -> Path:
    target = _exclusive_path(
        backup_dir,
        f"{source.stem}-pre-migration-{_timestamp()}-{digest[:12]}.json",
    )
    with source.open("rb") as input_file, target.open("xb") as output_file:
        output_file.write(input_file.read())
        output_file.flush()
        os.fsync(output_file.fileno())
    target.chmod(0o600)
    return target


def _archive_source(source: Path, backup_dir: Path, digest: str) -> Path:
    target = _exclusive_path(
        backup_dir,
        f"{source.stem}-migrated-{_timestamp()}-{digest[:12]}.bak",
    )
    os.replace(source, target)
    target.chmod(0o600)
    return target


def migrate_legacy_config(
    database: Database,
    source: Optional[Union[str, Path]] = None,
    backup_dir: Optional[Union[str, Path]] = None,
) -> LegacyMigrationResult:
    """在需要时将旧 JSON 原子导入 SQLite，并归档源文件。"""

    source_path = resolve_storage_path(source or settings.mcpcat_config_path)
    backup_path = resolve_storage_path(backup_dir or settings.mcpcat_backups_path)
    lock = FileLock(
        str(database.path.with_suffix(database.path.suffix + ".migration.lock")),
        timeout=settings.mcpcat_legacy_migration_lock_timeout_sec,
    )
    try:
        with lock:
            if not source_path.exists():
                return LegacyMigrationResult(state="no_legacy_file")

            raw = source_path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise LegacyMigrationError("旧配置不是有效的 UTF-8 JSON") from error
            config = _validate_legacy_config(parsed)

            with UnitOfWork(database) as unit:
                history = unit.migrations.find(MIGRATION_TYPE, digest)
                if history is not None and history.status == "completed":
                    archive = _archive_source(source_path, backup_path, digest)
                    return LegacyMigrationResult(
                        state="archive_recovered",
                        source_sha256=digest,
                        archive_path=archive,
                    )

            if _database_has_business_data(database):
                logger.info("SQLite 已有业务数据，跳过旧配置导入")
                return LegacyMigrationResult(
                    state="skipped_existing_database", source_sha256=digest
                )

            preserved = _backup_source(source_path, backup_path, digest)
            try:
                with UnitOfWork(database) as unit:
                    history = unit.migrations.find(MIGRATION_TYPE, digest)
                    if history is None:
                        history = unit.migrations.start(
                            MIGRATION_TYPE, str(source_path), digest
                        )
                    else:
                        history.status = "running"
                        history.error_message = None
                        history.completed_at = None
                    counters = _import_config(unit, config)
                    if unit.session is None:
                        raise RuntimeError("迁移事务未初始化")
                    unit.session.flush()
                    rebuilt = _rebuild_config(unit)
                    if _normalize_for_comparison(rebuilt) != _normalize_for_comparison(
                        config
                    ):
                        raise LegacyMigrationError("迁移事务内配置等价校验失败")
                    unit.migrations.complete(history, counters)
                    unit.commit()
            except Exception as error:
                with UnitOfWork(database) as unit:
                    failed = unit.migrations.find(MIGRATION_TYPE, digest)
                    if failed is None:
                        failed = unit.migrations.start(
                            MIGRATION_TYPE, str(source_path), digest
                        )
                    unit.migrations.fail(failed, type(error).__name__)
                    unit.commit()
                raise

            archive = _archive_source(source_path, backup_path, digest)
            logger.info(
                "旧配置迁移完成：source_sha256=%s，mcp=%d，api_keys=%d",
                digest[:12],
                counters["mcp_servers"],
                counters["api_keys"],
            )
            return LegacyMigrationResult(
                state="migrated",
                source_sha256=digest,
                backup_path=preserved,
                archive_path=archive,
            )
    except Timeout as error:
        raise LegacyMigrationError("等待旧配置迁移锁超时") from error


def export_legacy_config(database: Database, target: Union[str, Path]) -> Path:
    """从 SQLite 导出旧格式 JSON，且绝不覆盖已有目标。"""

    target_path = resolve_storage_path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with UnitOfWork(database) as unit:
        config = _rebuild_config(unit)
    payload = json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    with target_path.open("xb") as output_file:
        output_file.write(payload)
        output_file.flush()
        os.fsync(output_file.fileno())
    target_path.chmod(0o600)
    return target_path
