"""同一版本的制品写入、变体登记和发布共享互斥边界。"""

from filelock import FileLock

from app.core.config import settings
from app.storage.database import Database


def version_content_lock(database: Database, version_id: int) -> FileLock:
    """锁只保护内容版本，不包含任何凭证或用户上传路径。"""
    return FileLock(
        str(database.path) + f".skill-version-{version_id}.lock",
        timeout=max(1, settings.mcpcat_sqlite_busy_timeout_ms / 1000),
        is_singleton=True,
    )
