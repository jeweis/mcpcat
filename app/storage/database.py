"""SQLite engine、Session 与路径初始化。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Union

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_storage_path(value: Union[str, Path]) -> Path:
    """将持久化配置路径解析为绝对路径。"""

    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


class Database:
    """封装 SQLite engine 和事务 Session。"""

    def __init__(
        self,
        path: Optional[Union[str, Path]] = None,
        *,
        busy_timeout_ms: Optional[int] = None,
    ) -> None:
        self.path = resolve_storage_path(path or settings.mcpcat_database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = (
            busy_timeout_ms
            if busy_timeout_ms is not None
            else settings.mcpcat_sqlite_busy_timeout_ms
        )
        self.engine = self._create_engine()
        self._session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
        )

    def _create_engine(self) -> Engine:
        engine = create_engine(
            f"sqlite+pysqlite:///{self.path}",
            connect_args={"check_same_thread": False},
            future=True,
        )

        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
            cursor.close()

        return engine

    def new_session(self) -> Session:
        """创建由调用方负责关闭的 Session。"""

        return self._session_factory()

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        """在成功时提交、失败时回滚并始终关闭 Session。"""

        session = self.new_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        """释放数据库连接池。"""

        self.engine.dispose()
