"""Search Transform 前的原始 Catalog 目录，进一步缩小而不扩张范围。"""

from collections.abc import Callable, Sequence
from typing import Any

from fastmcp.tools import Tool

from app.models.code_mode_config import CodeModeConfig
from app.models.mcp_config import CatalogConfig
from app.services.catalog_provider import CatalogProvider
from app.services.code_mode_settings import code_mode_config
from app.services.config_service import ConfigService

ORIGIN_META = "io.mcpcat/tool-origin"
GATEWAY_COMPONENT_META = "io.mcpcat/gateway-component"


def current_catalog_config() -> CatalogConfig:
    """复用原 Catalog 目录配置，enabled 只控制原 Catalog 路由。"""
    return CatalogConfig(**(ConfigService.get_setting_section("catalog", {}) or {}))


class CodeModeProvider(CatalogProvider):
    """每次读取重新检查授权、配置和来源；不缓存 Tool 或凭证。"""

    def __init__(
        self,
        manager: Any,
        *,
        authorize: Callable[[], bool],
        dispatch_guard: Callable[[Tool], bool] | None = None,
        lookup_allowed: Callable[[], bool] | None = None,
        config_loader: Callable[[], CodeModeConfig] = code_mode_config,
        catalog_loader: Callable[[], CatalogConfig] = current_catalog_config,
    ) -> None:
        super().__init__(manager, catalog_loader())
        self._authorize = authorize
        self._dispatch_guard = dispatch_guard
        self._lookup_allowed = lookup_allowed
        self._code_loader = config_loader
        self._catalog_loader = catalog_loader

    def _active_config(self) -> CodeModeConfig | None:
        if not self._authorize():
            return None
        config = self._code_loader()
        return config

    def _eligible_servers(self) -> list[tuple[str, dict[str, Any]]]:
        config = self._active_config()
        if config is None:
            return []
        # 先让原 Catalog 排除整个命名空间的冲突，再缩小结果。
        # 否则隐藏一个来源可能重新启用原 Catalog 已拒绝的歧义别名。
        return super()._eligible_servers()

    def _wrap_tool(self, server_name: str, config: dict, tool: Tool) -> Tool:
        wrapped = super()._wrap_tool(server_name, config, tool)
        component = self._server_manager.server_info.get(server_name, {}).get(
            "gateway_component"
        )
        meta = dict(wrapped.meta or {})
        if component is not None:
            meta[GATEWAY_COMPONENT_META] = component
        return wrapped.model_copy(
            update={
                "meta": {
                    **meta,
                    ORIGIN_META: {"service": server_name, "tool": tool.name},
                }
            }
        )

    def _allowed_tool(self, tool: Tool, config: CodeModeConfig) -> bool:
        meta = tool.get_meta()
        origin = meta.get(ORIGIN_META, {})
        if (
            config.allowed_services is not None
            and origin.get("service") not in config.allowed_services
        ):
            return False
        if config.allowed_tools is not None and tool.name not in config.allowed_tools:
            return False
        # Apps 打开/回调不进入编排；未知 ui 声明也不擅自解释为可执行。
        if "ui" in meta or "ui/resourceUri" in meta:
            return False
        return meta.get(GATEWAY_COMPONENT_META) not in {"code-mode", "catalog", "apps"}

    async def _list_tools(self) -> Sequence[Tool]:
        """取当前原始工具，不经过 Catalog Search 的 synthetic 工具。"""
        self._config = self._catalog_loader()
        if self._active_config() is None:
            return []
        tools = await super()._list_tools()
        current = self._active_config()
        return (
            [tool for tool in tools if self._allowed_tool(tool, current)]
            if current
            else []
        )

    async def _get_tool(self, name: str, version=None) -> Tool | None:
        """直猜隐藏名称仍经过当前范围，不使用此前目录延长权限。"""
        self._config = self._catalog_loader()
        if self._lookup_allowed is not None and not self._lookup_allowed():
            return None
        current = self._active_config()
        if current is None or not self._name_in_scope(name, current):
            return None
        tool = await super()._get_tool(name, version)
        current = self._active_config()
        return (
            tool
            if tool is not None
            and current
            and self._allowed_tool(tool, current)
            and (self._dispatch_guard is None or self._dispatch_guard(tool))
            else None
        )

    def _name_in_scope(self, name: str, config: CodeModeConfig) -> bool:
        """明显不在允许范围的直猜名称，在访问上游描述之前拒绝。"""
        if config.allowed_tools is not None and name not in config.allowed_tools:
            return False
        return config.allowed_services is None or any(
            name.startswith(f"{service}__") for service in config.allowed_services
        )
