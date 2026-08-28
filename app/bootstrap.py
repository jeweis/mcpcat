"""存储优先的应用 Bootstrap。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from app.services.config_service import ConfigService
from app.storage.database import Database
from app.storage.legacy_migration import LegacyMigrationResult, migrate_legacy_config
from app.storage.migrations import SchemaStatus, upgrade_schema
from app.storage.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BootstrapResult:
    """成功完成的存储 Bootstrap 状态。"""

    database: Database
    schema: SchemaStatus
    legacy: LegacyMigrationResult


def bootstrap_storage(database: Optional[Database] = None) -> BootstrapResult:
    """按 Schema → legacy migration → facade 的顺序初始化存储。"""

    active_database = database or Database()
    try:
        schema = upgrade_schema(active_database)
        legacy = migrate_legacy_config(active_database)
        ConfigService.configure_database(active_database)
        with UnitOfWork(active_database) as unit:
            is_empty = not (
                unit.mcp_servers.list_configs()
                or unit.api_keys.list_all()
                or unit.settings.list_all()
            )
        if is_empty:
            if not ConfigService.save_config(ConfigService._create_default_config()):
                raise RuntimeError("无法写入默认 SQLite 配置")
        logger.info(
            "存储初始化完成：schema=%s legacy=%s",
            schema.current,
            legacy.state,
        )
        return BootstrapResult(active_database, schema, legacy)
    except Exception:
        ConfigService.reset_database()
        active_database.dispose()
        logger.exception("存储初始化失败，业务服务不会启动")
        raise
