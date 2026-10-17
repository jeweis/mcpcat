"""独立 Skills MCP 的网关挂载、实时鉴权与公开生命周期适配。"""

import hashlib
import hmac
import secrets
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app.models.mcp_config import PermissionType
from app.models.skills_mcp_config import SkillsMCPConfig
from app.services.config_service import ConfigService
from app.services.remote_skill_registry import RemoteSkillRegistry, SkillEndpointScope
from app.services.registry_subscriptions import RegistrySubscriptions, VisibleSnapshot
from app.services.security_service import security_service
from app.services.skills_mcp_extension import (
    RegistrySkillResources,
    RegistrySkillsExtension,
)
from app.storage.database import Database
from app.storage.runtime_identities import endpoint_identity, instance_identity


def skills_mcp_config() -> SkillsMCPConfig:
    """从当前配置读取，非法配置不默默退化为公开端点。"""
    return SkillsMCPConfig.model_validate(
        ConfigService.get_setting_section("skills_mcp", {}) or {}
    )


def validate_skills_endpoint_path(config: SkillsMCPConfig, manager: Any) -> None:
    """启用前验证配置资源，不执行用户服务或覆盖已有路径。"""
    from app.services.platform_endpoints import validate_platform_path

    validate_platform_path("skills_mcp", config.path_name, manager)


class SkillsMCPRuntime:
    """不把独立端点写成普通用户 MCP 服务，旧配置/列表不受污染。"""

    def __init__(self, database: Database, manager: Any) -> None:
        self.database, self.manager = database, manager
        self.config = skills_mcp_config()
        self.alive = False
        self.http_app = None
        self.scope = None
        self._cursor_salt = secrets.token_bytes(32)
        self.attachments = {}
        self._app = None
        validate_skills_endpoint_path(self.config, manager)
        self.scope = SkillEndpointScope(
            instance_identity(database), endpoint_identity(database, "skills")
        )
        server = FastMCP("mcpcat Skills")
        server.add_extension(
            RegistrySkillsExtension(
                self.registry, principal=self.principal, page_size=self.config.page_size
            )
        )
        server.add_provider(RegistrySkillResources(self.registry))
        self.subscriptions = RegistrySubscriptions(
            database,
            self.subscription_snapshot,
            self.subscription_authorized,
            principal=self.principal,
        )
        server.add_middleware(self.subscriptions)
        self.http_app = server.http_app(path="/", json_response=True)

    def subscription_authorized(self) -> bool:
        """持续按当前端点配置重新鉴权，不沿用握手时的权限。"""
        if not self.available():
            return False
        supplied_key = get_http_request().headers.get(
            security_service.get_auth_header_name(), ""
        )
        if supplied_key or skills_mcp_config().require_auth:
            return self.authorize()
        return True

    async def subscription_snapshot(self) -> VisibleSnapshot:
        """只使用协议 Registry 当前身份可见的文件及摘要。"""
        return VisibleSnapshot(
            resources={
                item["uri"]: item["digest"]
                for entry in self.registry().list_skills()
                for item in entry["resources"]
            }
        )

    def available(self) -> bool:
        """生命周期结束或改路径立即失效；新路径需重启挂载。"""
        try:
            current = skills_mcp_config()
            return bool(self.alive and current.path_name == self.config.path_name)
        except ValueError:
            return False

    def attach_service(self, name: str, server: FastMCP) -> Any:
        """所有新建/重启来源均在 HTTP 应用生成前注册。"""
        return self._attach(server, "service", name)

    def source_enabled(self) -> bool:
        """来源扩展固定注册，非法配置与生命周期结束拒绝分发。"""
        try:
            skills_mcp_config()
            return self.alive
        except ValueError:
            return False

    def attach_catalog(self, server: FastMCP) -> Any:
        """Catalog 仍使用原 Search Transform 工具契约。"""
        return self._attach(server, "catalog")

    def _attach(self, server: FastMCP, kind: str, source: str = "") -> Any:
        from app.services.skills_source_attachment import SkillsSourceAttachment

        attachment = SkillsSourceAttachment(self, server, kind, source)
        self.attachments[attachment.scope.binding] = attachment
        if self._app is not None:
            self._register_source_scope(attachment)
        return attachment

    def _register_source_scope(self, attachment: Any) -> None:
        path = (
            attachment.source
            if attachment.kind == "service"
            else (ConfigService.get_setting_section("catalog", {}) or {}).get(
                "path_name", "mcpcat"
            )
        )
        self._app.state.skills_mcp_scopes[attachment.scope.binding] = {
            "path_name": path,
            "enabled": True,
            "require_auth": attachment.require_auth(),
        }

    def source_available(self, binding: str) -> bool:
        """管理接入信息只相信当前实际注册的来源附件。"""
        attachment = self.attachments.get(binding)
        return attachment is not None and attachment.available()

    def _key(self):
        try:
            request = get_http_request()
        except RuntimeError:
            return None
        key = security_service.verify_api_key(
            request.headers.get(security_service.get_auth_header_name(), "")
        )
        return (
            key
            if key and security_service.has_permission(key, PermissionType.READ)
            else None
        )

    def authorize(self) -> bool:
        """不相信旧 request.state 或 cursor，重新验证启用/过期及 READ。"""
        return self._key() is not None

    def principal(self) -> str:
        """游标只记录有盐摘要，名字相同的两个 Key 仍是不同身份。"""
        key = self._key()
        return (
            hmac.new(self._cursor_salt, key.key.encode(), hashlib.sha256).hexdigest()
            if key
            else "anonymous"
        )

    def registry(self) -> RemoteSkillRegistry:
        """请求内使用当前 require_auth；WRITE 不授予协议草稿可见性。"""
        current = skills_mcp_config()
        scope = replace(self.scope, require_auth=current.require_auth)
        return RemoteSkillRegistry(
            self.database, scope, authorize=self.authorize, available=self.available
        )

    def mount(self, app: FastAPI) -> None:
        """注册实际连接元数据，仅在 lifespan 启动后报告 ready。"""
        app.state.skills_mcp_runtime = self
        app.state.skills_mcp_scopes = {}
        self._app = app
        for attachment in self.attachments.values():
            self._register_source_scope(attachment)
        if self.http_app is not None:
            app.mount(f"/mcp/{self.config.path_name}", self)
            app.state.skills_mcp_scopes[""] = {
                "path_name": self.config.path_name,
                "enabled": True,
                "require_auth": self.config.require_auth,
            }

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """父应用管理公开 HTTP lifespan，异常退出也清除就绪状态。"""
        if self.http_app is None:
            self.alive = True
            try:
                yield
            finally:
                self.alive = False
            return
        async with self.http_app.lifespan(self.http_app):
            self.alive = True
            try:
                yield
            finally:
                self.alive = False
                await self.subscriptions.legacy.close()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """生命周期结束或路径变化后不继续声明已失效端点。"""
        if not self.available():
            await JSONResponse(
                {"detail": "Skills MCP endpoint unavailable"}, status_code=404
            )(scope, receive, send)
            return
        await self.http_app(scope, receive, send)
