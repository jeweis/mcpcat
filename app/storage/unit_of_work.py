"""事务化 Unit of Work。"""

from __future__ import annotations

from types import TracebackType
from typing import Optional, Type

from sqlalchemy.orm import Session

from app.storage.database import Database
from app.storage.repositories import (
    APIKeyRepository,
    MCPServerRepository,
    MigrationHistoryRepository,
    SettingsRepository,
)
from app.storage.skill_repositories import (
    SkillArtifactRepository,
    SkillAuditRepository,
    SkillBundleRepository,
    SkillRepository,
    SkillVersionRepository,
)


class UnitOfWork:
    """在一个 Session 内组合 Repository 并管理提交或回滚。"""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.session: Optional[Session] = None

    def __enter__(self) -> "UnitOfWork":
        self.session = self.database.new_session()
        self.mcp_servers = MCPServerRepository(self.session)
        self.api_keys = APIKeyRepository(self.session)
        self.settings = SettingsRepository(self.session)
        self.migrations = MigrationHistoryRepository(self.session)
        self.skills = SkillRepository(self.session)
        self.skill_versions = SkillVersionRepository(self.session)
        self.skill_artifacts = SkillArtifactRepository(self.session)
        self.skill_bundles = SkillBundleRepository(self.session)
        self.skill_audit = SkillAuditRepository(self.session)
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        if self.session is None:
            return
        try:
            if exc_type is not None:
                self.session.rollback()
        finally:
            self.session.close()
            self.session = None

    def commit(self) -> None:
        """提交当前事务。"""

        if self.session is None:
            raise RuntimeError("UnitOfWork 尚未进入上下文")
        self.session.commit()

    def rollback(self) -> None:
        """回滚当前事务。"""

        if self.session is None:
            raise RuntimeError("UnitOfWork 尚未进入上下文")
        self.session.rollback()
