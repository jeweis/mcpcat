"""服务/Catalog Skills 适配，实时工具约束与能力声明使用公开 API。"""

from dataclasses import replace
from typing import Any

from fastmcp import FastMCP
from fastmcp.resources import ResourceResult
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.providers import FastMCPProvider
from fastmcp.server.dependencies import get_http_request
from mcp_types import DiscoverResult

from app.services.config_service import ConfigService
from app.services.registry_subscriptions import RegistrySubscriptions, VisibleSnapshot
from app.services.security_service import security_service
from app.services.subscription_context import subscription_snapshot_read
from app.services.remote_skill_registry import RemoteSkillRegistry, SkillEndpointScope
from app.services.skills_mcp_extension import (
    SKILLS_EXTENSION,
    RegistrySkillResources,
    RegistrySkillsExtension,
    read_registry_resource,
)
from app.storage.runtime_identities import (
    current_endpoint_identity,
    endpoint_identity,
    instance_identity,
)


class SkillsCapabilityGate(Middleware):
    """只过滤本平台拥有的 Skills 声明，不修改上游未知元数据或 UI 扩展。"""

    def __init__(self, attachment: "SkillsSourceAttachment") -> None:
        self.attachment = attachment

    async def on_discover(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> DiscoverResult | dict:
        """关闭扩展时原工具/资源仍继续提供。"""
        result = await call_next(context)
        if self.attachment.available():
            return result
        if isinstance(result, dict):
            result = DiscoverResult.model_validate(result)
        extensions = dict(result.capabilities.extensions or {})
        extensions.pop(SKILLS_EXTENSION, None)
        return result.model_copy(
            update={
                "capabilities": result.capabilities.model_copy(
                    update={"extensions": extensions}
                )
            }
        )

    async def on_read_resource(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> ResourceResult:
        """自有命名空间优先且失败不回退上游，避免同名静态资源劫持。"""
        uri = str(context.message.uri)
        if uri.startswith("skill://mcpcat/"):
            return await read_registry_resource(self.attachment.registry, uri)
        return await call_next(context)


class SkillsSourceAttachment:
    """绑定不可变来源 UUID；重启允许延续，删除重建必须重新审核变体。"""

    def __init__(
        self, runtime: Any, server: FastMCP, kind: str, source: str = ""
    ) -> None:
        self.runtime, self.server, self.kind, self.source = (
            runtime,
            server,
            kind,
            source,
        )
        self.scope = SkillEndpointScope(
            instance_identity(runtime.database),
            endpoint_identity(runtime.database, kind, source),
            kind=kind,
            source_name=source,
        )
        self.scope = replace(self.scope, binding=self.scope.endpoint)
        server.add_extension(
            RegistrySkillsExtension(
                self.registry,
                principal=runtime.principal,
                page_size=runtime.config.page_size,
            )
        )
        server.add_provider(RegistrySkillResources(self.registry))
        server.add_middleware(SkillsCapabilityGate(self))
        self.subscriptions = None

    def install_subscriptions(self) -> None:
        """本地生成的来源没有上游事件流，直接发布平台变更通知。"""
        self.subscriptions = RegistrySubscriptions(
            self.runtime.database,
            self.subscription_snapshot,
            self.subscription_authorized,
            principal=self.runtime.principal,
            legacy_schedule=lambda job: self.runtime.manager.lifecycle.schedule(
                job, self.source
            ),
        )
        self.server.add_middleware(self.subscriptions)

    def subscription_authorized(self) -> bool:
        """持续验证来源代际及显式 Key，不将失效 Key 降级为匿名。"""
        if not self.available():
            return False
        key = get_http_request().headers.get(
            security_service.get_auth_header_name(), ""
        )
        return self.runtime.authorize() if key or self.require_auth() else True

    async def subscription_snapshot(self) -> VisibleSnapshot:
        """读取当前来源中可见的 Skill 清单，不执行工具。"""
        with subscription_snapshot_read():
            registry = await self.registry()
        return VisibleSnapshot(
            resources={
                item["uri"]: item["digest"]
                for entry in registry.list_skills()
                for item in entry["resources"]
            }
        )

    def available(self) -> bool:
        """逐请求检查运行状态、配置和当前实例，旧附件不能串到新服务。"""
        if not self.runtime.source_enabled():
            return False
        if (
            current_endpoint_identity(self.runtime.database, self.kind, self.source)
            != self.scope.endpoint
        ):
            return False
        if self.kind == "service":
            info = self.runtime.manager.server_info.get(self.source, {})
            config = ConfigService.get_server_config(self.source)
            return (
                info.get("status") == "running"
                and info.get("mcp") is self.server
                and config is not None
                and config.get("enabled", True)
            )
        catalog = self.runtime.manager.catalog_service
        config = ConfigService.get_setting_section("catalog", {}) or {}
        return (
            catalog is not None
            and catalog.owns_server(self.server)
            and config.get("enabled", True)
        )

    def require_auth(self) -> bool:
        """依照当前端点配置，不因独立 Skills 端点公开而放开来源端点。"""
        if self.kind == "service":
            return (ConfigService.get_server_config(self.source) or {}).get(
                "require_auth", True
            )
        return (ConfigService.get_setting_section("catalog", {}) or {}).get(
            "require_auth", True
        )

    async def registry(self) -> RemoteSkillRegistry:
        """工具目录来自当前 Provider；绑定缺失不能退到其他来源。"""
        names = set()
        if self.available():
            try:
                names = {
                    tool.name
                    for tool in await FastMCPProvider(self.server).list_tools()
                }
            except Exception:
                # 不记录上游异常正文，目录无法校验时拒绝提供 Skill。
                return self._registry(set(), available=False)
        return self._registry(names)

    async def metadata_eligible(self, metadata: dict) -> bool:
        """管理连接提示也校验实时工具，不能仅凭端点在运行就报告可用。"""
        if not self.available():
            return False
        try:
            names = {
                tool.name for tool in await FastMCPProvider(self.server).list_tools()
            }
        except Exception:
            return False
        required = metadata.get("required_tools", [])
        return isinstance(required, list) and all(
            isinstance(name, str) and name in names for name in required
        )

    def _registry(self, names: set[str], available: bool = True) -> RemoteSkillRegistry:
        def eligible(metadata: dict) -> bool:
            required = metadata.get("required_tools", [])
            return isinstance(required, list) and all(
                isinstance(name, str) and name in names for name in required
            )

        return RemoteSkillRegistry(
            self.runtime.database,
            replace(self.scope, require_auth=self.require_auth()),
            authorize=self.runtime.authorize,
            available=lambda: available and self.available(),
            eligible=eligible,
        )
