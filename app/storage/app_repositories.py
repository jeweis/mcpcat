"""Apps 事务仓储：默认草稿且发布后拒绝更新绑定。"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage.app_models import AppRecord, AppToolBindingRecord, AppVersionRecord
from app.storage.skill_repositories import validate_semver, validate_skill_slug


class AppRepository:
    """复用现有事务边界，不隐式 commit 或发布。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, slug: str) -> AppRecord | None:
        """按稳定 slug 查找管理记录。"""
        return self.session.scalar(select(AppRecord).where(AppRecord.slug == slug))

    def create(
        self, *, slug: str, display_name: str, actor: str | None = None
    ) -> AppRecord:
        """创建未公开、未发布的 App。"""
        validate_skill_slug(slug)
        if self.get(slug):
            raise ValueError("同名 App 已存在")
        row = AppRecord(slug=slug, display_name=display_name, created_by=actor)
        self.session.add(row)
        self.session.flush()
        return row

    def get_version(self, app_id: int, version: str) -> AppVersionRecord | None:
        """查找明确版本，不能把缺失版本解释成 latest。"""
        return self.session.scalar(
            select(AppVersionRecord).where(
                AppVersionRecord.app_id == app_id,
                AppVersionRecord.version == version,
            )
        )

    def create_version(
        self,
        *,
        app_id: int,
        version: str,
        content_format: str,
        entrypoint: str,
        manifest: dict[str, Any],
    ) -> AppVersionRecord:
        """创建格式明确的草稿版本。"""
        validate_semver(version)
        if content_format not in {"prefab", "html", "upstream"}:
            raise ValueError("不支持的 App 格式")
        if self.get_version(app_id, version):
            raise ValueError("App 版本已存在")
        row = AppVersionRecord(
            app_id=app_id,
            version=version,
            content_format=content_format,
            entrypoint=entrypoint,
            manifest_json=manifest,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def bind_tool(
        self, *, version_id: int, alias: str, server_name: str, tool_name: str
    ) -> AppToolBindingRecord:
        """仅给草稿绑定工具；实际授权在发布和每次调用时校验。"""
        version = self.session.get(AppVersionRecord, version_id)
        if not version or version.status != "draft":
            raise ValueError("只能修改 App 草稿的工具绑定")
        if not alias or not server_name or not tool_name:
            raise ValueError("工具绑定必须有别名和明确来源")
        row = AppToolBindingRecord(
            app_version_id=version_id,
            alias=alias,
            server_name=server_name,
            tool_name=tool_name,
        )
        self.session.add(row)
        self.session.flush()
        return row
