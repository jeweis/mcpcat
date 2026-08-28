"""Alembic Schema 版本检查与安全升级。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from app.core.config import settings
from app.storage.database import Database, resolve_storage_path
from app.storage.maintenance import backup_database

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class SchemaStatus:
    """数据库当前与目标 Schema 版本。"""

    current: Optional[str]
    head: str

    @property
    def upgrade_required(self) -> bool:
        return self.current != self.head


def create_alembic_config(database_path: Path) -> Config:
    """创建指向指定 SQLite 数据库的 Alembic 配置。"""

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option(
        "sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}"
    )
    return config


def get_schema_status(database: Database) -> SchemaStatus:
    """读取数据库当前 revision 和迁移脚本 head。"""

    config = create_alembic_config(database.path)
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("Alembic 未定义 head revision")
    with database.engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    return SchemaStatus(current=current, head=head)


def upgrade_schema(database: Database) -> SchemaStatus:
    """必要时备份现有数据库，并升级到最新 Schema。"""

    status = get_schema_status(database)
    if not status.upgrade_required:
        return status
    if database.path.exists() and database.path.stat().st_size > 0:
        backup_database(
            database.path,
            resolve_storage_path(settings.mcpcat_backups_path),
            label="pre-schema-upgrade",
        )
    command.upgrade(create_alembic_config(database.path), "head")
    upgraded = get_schema_status(database)
    if upgraded.upgrade_required:
        raise RuntimeError(
            f"数据库 Schema 升级不完整：{upgraded.current} != {upgraded.head}"
        )
    return upgraded
