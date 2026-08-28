"""SQLite 备份、完整性和状态诊断。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union

from sqlalchemy import text

from app.core.config import settings
from app.storage.database import Database, resolve_storage_path


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backup_database(
    source: Union[str, Path],
    backup_dir: Optional[Union[str, Path]] = None,
    *,
    label: str = "backup",
) -> Optional[Path]:
    """使用 SQLite backup API 创建一致性备份。"""

    source_path = resolve_storage_path(source)
    if not source_path.exists() or source_path.stat().st_size == 0:
        return None
    target_dir = resolve_storage_path(backup_dir or settings.mcpcat_backups_path)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"mcpcat-{label}-{_timestamp()}.db"
    suffix = 1
    while target.exists():
        target = target_dir / f"mcpcat-{label}-{_timestamp()}-{suffix}.db"
        suffix += 1
    with sqlite3.connect(source_path) as source_connection:
        with sqlite3.connect(target) as target_connection:
            source_connection.backup(target_connection)
    target.chmod(0o600)
    return target


def check_integrity(database: Database) -> str:
    """运行 SQLite integrity_check 并返回结果。"""

    with database.engine.connect() as connection:
        return str(connection.execute(text("PRAGMA integrity_check")).scalar_one())


def get_storage_status(database: Database) -> Dict[str, Any]:
    """返回不含业务秘密的数据库运行状态。"""

    with database.engine.connect() as connection:
        journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
        busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar_one()
    return {
        "path": str(database.path),
        "exists": database.path.exists(),
        "size": database.path.stat().st_size if database.path.exists() else 0,
        "integrity": check_integrity(database),
        "journal_mode": journal_mode,
        "foreign_keys": bool(foreign_keys),
        "busy_timeout_ms": int(busy_timeout),
    }
