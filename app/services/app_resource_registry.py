"""固定实例/端点的已发布 App 资源，读时重新核对授权与来源。"""

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.services.app_content_service import AppDomainError, app_files, load_app_content
from app.services.app_rendering import app_callback_name as app_callback_name
from app.services.app_rendering import render_app
from app.services.app_resource_uri import AppResourceURI
from app.storage.app_models import AppRecord, AppVersionRecord
from app.storage.database import Database
from app.storage.runtime_identities import current_endpoint_identity
from app.storage.unit_of_work import UnitOfWork


class AppResourceRegistry:
    """调用者必须提供当前身份、开关和实时工具资格验证。"""

    def __init__(
        self,
        database: Database,
        *,
        instance: str,
        endpoint: str,
        authorize: Callable[[], bool],
        available: Callable[[], bool],
        eligible: Callable[[str, str, dict], Awaitable[bool]],
        public_only: Callable[[], bool] = lambda: False,
    ) -> None:
        self.database, self.instance, self.endpoint = database, instance, endpoint
        self.authorize, self.available, self.eligible = authorize, available, eligible
        self.public_only = public_only

    def check(self) -> None:
        """旧 Provider 不能借当前开关/已失效端点延长读取权限。"""
        if (
            not self.available()
            or not self.authorize()
            or current_endpoint_identity(self.database, "apps") != self.endpoint
        ):
            raise AppDomainError("App 资源不可用", 404)

    def published(self) -> list[AppResourceURI]:
        """发现只给固定已发布上传版本，不包含草稿/撤销或源码正文。"""
        self.check()
        with UnitOfWork(self.database) as unit:
            rows = unit.session.execute(
                select(AppRecord.id, AppVersionRecord.version)
                .join(AppVersionRecord, AppVersionRecord.app_id == AppRecord.id)
                .where(
                    AppRecord.status == "published",
                    AppRecord.source_type == "uploaded",
                    AppVersionRecord.status == "published",
                    AppRecord.visibility == "public" if self.public_only() else True,
                )
                .order_by(AppRecord.id, AppVersionRecord.id)
            ).all()
        self.check()
        return [
            AppResourceURI(self.instance, self.endpoint, app_id, version)
            for app_id, version in rows
        ]

    async def load(self, uri: str) -> tuple[AppResourceURI, str, dict, bytes]:
        """直接 URI 不依赖 listing，身份和冻结版本必须严格匹配。"""
        self.check()
        identity = AppResourceURI.parse(uri)
        if identity.instance != self.instance or identity.endpoint != self.endpoint:
            raise AppDomainError("App 资源身份不匹配", 404)
        with UnitOfWork(self.database) as unit:
            app = unit.session.get(AppRecord, identity.app_id)
            if (
                not app
                or app.source_type != "uploaded"
                or (self.public_only() and app.visibility != "public")
            ):
                raise AppDomainError("App 不存在", 404)
            slug = app.slug
        info, content = await run_in_threadpool(
            load_app_content, self.database, slug=slug, version=identity.version
        )
        self.check()
        if not await self.eligible(slug, identity.version, info):
            raise AppDomainError("App 绑定来源不可用", 404)
        self.check()
        return identity, slug, info, content

    async def entry(self, uri: str) -> tuple[str, dict[str, Any], dict]:
        """Prefab renderer 与动态 payload 分开返回，原始文件保持不变。"""
        identity, slug, info, content = await self.load(uri)
        if identity.path is not None:
            raise AppDomainError("不是 App 入口 URI")
        rendered = await run_in_threadpool(render_app, info, content, identity.app_id)
        await self.load(uri)
        return rendered.html, rendered.structured_content, rendered.metadata

    async def file(self, uri: str) -> bytes:
        """附件仅从已验证包中读取，不按 URI 访问宿主文件系统。"""
        identity, slug, info, content = await self.load(uri)
        if identity.path is None:
            raise AppDomainError("不是 App 附件 URI")
        files = await run_in_threadpool(app_files, info, content)
        data = files.get(identity.path)
        if data is None:
            raise AppDomainError("App 附件不存在", 404)
        await self.load(uri)
        return data
