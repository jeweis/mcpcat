"""Apps 独立 MCP HTTP 入口，当前请求身份与匿名审核分别验证。"""

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app.models.mcp_config import PermissionType
from app.services.app_audit import app_audit_actor
from app.services.app_mcp_cancellation import AppDisconnectMiddleware
from app.services.app_resource_registry import AppResourceRegistry
from app.services.app_resources_provider import AppResourcesProvider
from app.services.app_tool_bindings import AppToolResolver, publication_validator
from app.services.app_tools_provider import AppToolsProvider
from app.services.apps_mcp_settings import apps_mcp_config, validate_apps_path
from app.services.security_service import security_service
from app.services.registry_subscriptions import RegistrySubscriptions, VisibleSnapshot
from app.storage.database import Database
from app.storage.runtime_identities import endpoint_identity, instance_identity


class AppsMCPRuntime:
    """固定挂载，版本 URI/Provider 随父应用管理生命周期。"""

    def __init__(self, database: Database, manager: Any) -> None:
        self.database, self.manager = database, manager
        self.config = apps_mcp_config()
        self.http_app = None
        self.alive = False
        validate_apps_path(self.config, manager)
        for name in manager.server_info:
            self.register_source(name)
        resolver = AppToolResolver(
            database,
            manager,
            self.authorize,
            allow_source=lambda name, config: self.authenticated()
            or not config.get("require_auth", True),
            principal=lambda: app_audit_actor(database, self._key()),
        )
        registry = AppResourceRegistry(
            database,
            instance=instance_identity(database),
            endpoint=endpoint_identity(database, "apps"),
            authorize=self.authorize,
            available=self.available,
            eligible=self.eligible,
            public_only=lambda: not self.authenticated(),
        )
        self.resolver, self.registry = resolver, registry
        self.resources_provider = AppResourcesProvider(registry)
        self.tools_provider = AppToolsProvider(registry, resolver)
        self.server = FastMCP(
            "mcpcat Apps",
            providers=[
                self.resources_provider,
                self.tools_provider,
            ],
        )
        self.subscriptions = RegistrySubscriptions(
            database,
            self.subscription_snapshot,
            self.authorize,
            supported=frozenset({"resources", "tools"}),
            principal=self._key,
        )
        self.server.add_middleware(self.subscriptions)
        self.http_app = self.server.http_app(path="/", json_response=True)
        self.server.add_middleware(AppDisconnectMiddleware())

    async def subscription_snapshot(self) -> VisibleSnapshot:
        """复用公开 Provider 资格检查，不调用 App 或绑定的业务工具。"""
        resources = await self.resources_provider.list_resources()
        tools = await self.tools_provider.list_tools()
        return VisibleSnapshot.from_components(resources, tools)

    def register_source(self, name: str) -> None:
        """显式创建/启动来源时登记 UUID；资源请求不创建已退休来源身份。"""
        endpoint_identity(self.database, "service", name)

    def available(self) -> bool:
        """退出生命周期或改路径立即拒绝，不假报挂载状态。"""
        try:
            current = apps_mcp_config()
            return bool(
                self.alive
                and self.http_app is not None
                and current.path_name == self.config.path_name
            )
        except ValueError:
            return False

    def _valid_key(self, key: str) -> bool:
        row = security_service.verify_api_key(key)
        return bool(row and security_service.has_permission(row, PermissionType.READ))

    def _key(self) -> str:
        try:
            return get_http_request().headers.get(
                security_service.get_auth_header_name(), ""
            )
        except RuntimeError:
            return ""

    def authenticated(self) -> bool:
        """每个读取与调用检查当前 Key，不信任第一次中间件快照。"""
        return self.available() and self._valid_key(self._key())

    def authorize(self) -> bool:
        """错误 Key 不回退匿名；匿名仍需 App 和来源分别公开审核。"""
        if not self.available():
            return False
        key = self._key()
        return self._valid_key(key) if key else not apps_mcp_config().require_auth

    async def eligible(self, slug: str, version: str, info: dict) -> bool:
        """已发布绑定也须通过当前源 UUID/Schema/权限检查。"""
        try:
            await publication_validator(self.database, self.resolver, slug, version)
            return self.authorize()
        except (ValueError, OSError):
            return False

    def mount(self, app: FastAPI) -> None:
        """系统入口不写入普通服务配置、列表或 Catalog。"""
        app.state.apps_mcp_runtime = self
        if self.http_app is not None:
            app.mount(f"/mcp/{self.config.path_name}", self)

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """远程入口真实启动后才报告 ready，关闭后停止新请求。"""
        if self.http_app is None:
            yield
            return
        async with self.http_app.lifespan(self.http_app):
            self.alive = True
            try:
                yield
            finally:
                self.alive = False
                await self.subscriptions.legacy.close()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI 层独立验证身份，不能借同名旧公共 Catalog 绕过。"""
        if not self.available():
            await JSONResponse(
                {"detail": "Apps endpoint unavailable"}, status_code=404
            )(scope, receive, send)
            return
        key = Headers(scope=scope).get(security_service.get_auth_header_name(), "")
        if (key and not self._valid_key(key)) or (
            not key and apps_mcp_config().require_auth
        ):
            await JSONResponse({"detail": "READ Key required"}, status_code=401)(
                scope, receive, send
            )
            return
        await self.http_app(scope, receive, send)
