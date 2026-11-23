"""Catalog 动态 Provider，聚合已纳入的 MCP 服务工具。"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from fastmcp.apps.config import is_model_visible
from fastmcp.server.providers import FastMCPProvider, Provider
from fastmcp.tools import Tool

from app.models.mcp_config import CatalogConfig
from app.services.subscription_context import subscription_snapshot_read

if TYPE_CHECKING:
    from app.services.server_manager import MCPServerManager

logger = logging.getLogger(__name__)

CATALOG_TOOL_SEPARATOR = "__"


def make_namespaced_name(server: str, tool_name: str) -> str:
    """生成 Catalog 对外工具名。"""
    return f"{server}{CATALOG_TOOL_SEPARATOR}{tool_name}"


class CatalogProvider(Provider):
    """实时读取 MCPServerManager 的稳定 Catalog Provider。"""

    def __init__(
        self,
        server_manager: "MCPServerManager",
        config: CatalogConfig,
    ) -> None:
        super().__init__()
        self._server_manager = server_manager
        self._config = config
        self._cache: Optional[List[Tool]] = None
        self._cache_at = 0.0
        self._cache_sig: Optional[Tuple[Any, ...]] = None
        self._cache_lock = asyncio.Lock()
        self._ttl = max(5, int(getattr(config, "refresh_interval_sec", 60)))

    def _eligible_servers(self) -> List[Tuple[str, Dict[str, Any]]]:
        """返回当前可纳入 Catalog 的服务，长名称优先用于无歧义路由。"""
        servers = []
        for name, info in self._server_manager.server_info.items():
            config = info.get("config", {})
            if info.get("status") != "running":
                continue
            if not config.get("expose_in_catalog", False):
                continue
            if config.get("oauth"):
                continue
            if info.get("mcp") is None:
                continue
            servers.append((name, info))
        return sorted(servers, key=lambda item: len(item[0]), reverse=True)

    def _description(
        self,
        server_name: str,
        config: Dict[str, Any],
        tool: Tool,
    ) -> str:
        """将可配置的服务元数据并入官方 Transform 可搜索描述。"""
        description = tool.description or ""
        if not self._config.include_server_meta_in_search:
            return description

        metadata = [f"MCP service: {server_name}."]
        note = config.get("note")
        if note:
            metadata.append(f"Service note: {note}.")
        tags = config.get("tags") or []
        if tags:
            tag_text = ", ".join(str(tag) for tag in tags)
            metadata.append(f"Service tags: {tag_text}.")
        return " ".join(part for part in [description, *metadata] if part)

    def _wrap_tool(
        self,
        server_name: str,
        config: Dict[str, Any],
        tool: Tool,
    ) -> Tool:
        """保留原始 Tool 行为，仅更新 Catalog 名称和可搜索描述。"""
        return tool.model_copy(
            update={
                "name": make_namespaced_name(server_name, tool.name),
                "description": self._description(server_name, config, tool),
            }
        )

    async def _list_server_tools(
        self,
        server_name: str,
        info: Dict[str, Any],
    ) -> Sequence[Tool]:
        provider = FastMCPProvider(info["mcp"])
        with subscription_snapshot_read():
            tools = await provider.list_tools()
        config = info.get("config", {})
        return [self._wrap_tool(server_name, config, tool) for tool in tools]

    def _dedicated_app(self, tool: Tool) -> bool:
        """显式启用 Apps 后由来源 Provider 提供打开动作，避免原始 URI 混入搜索。"""
        runtime = getattr(self._server_manager, "apps_runtime", None)
        if runtime is None or not runtime.available():
            return False
        from app.services.catalog_app_tools import app_uri

        return app_uri(tool) is not None

    def _signature(self) -> Tuple[Any, ...]:
        """来源集合/身份/状态签名；变化即失效，动态增删替换即时生效。"""
        return tuple(
            (name, id(info.get("mcp")), info.get("status"))
            for name, info in self._eligible_servers()
        )

    async def unfiltered_tools(self, force: bool = False) -> Sequence[Tool]:
        """按来源签名 + TTL 复用聚合结果，避免每次目录搜索都为每个来源启动上游进程。"""
        if (
            not force
            and self._cache is not None
            and self._cache_sig == self._signature()
            and time.monotonic() - self._cache_at < self._ttl
        ):
            return list(self._cache)
        async with self._cache_lock:
            sig = self._signature()
            if (
                not force
                and self._cache is not None
                and self._cache_sig == sig
                and time.monotonic() - self._cache_at < self._ttl
            ):
                return list(self._cache)
            tools = await self._compute_unfiltered_tools()
            self._cache = list(tools)
            self._cache_at = time.monotonic()
            self._cache_sig = sig
            return list(tools)

    async def force_refresh(self) -> Sequence[Tool]:
        """使缓存失效并立即重算（目录刷新/来源变更时调用）。"""
        self._cache = None
        self._cache_sig = None
        return await self.unfiltered_tools(force=True)

    async def _compute_unfiltered_tools(self) -> Sequence[Tool]:
        """在受众/App 分类之前排除整个来源命名空间的实际别名冲突。"""
        servers = self._eligible_servers()
        results = await asyncio.gather(
            *(self._list_server_tools(name, info) for name, info in servers),
            return_exceptions=True,
        )

        tools_by_name: Dict[str, Tool] = {}
        collisions = set()
        for (server_name, _), result in zip(servers, results):
            if isinstance(result, BaseException):
                logger.warning(
                    "读取 MCP 服务 '%s' 工具失败 (%s)",
                    server_name,
                    type(result).__name__,
                )
                continue
            for tool in result:
                if tool.name in tools_by_name:
                    collisions.add(tool.name)
                    continue
                tools_by_name[tool.name] = tool

        for name in collisions:
            tools_by_name.pop(name, None)
            logger.error("Catalog 工具名冲突，已排除: %s", name)
        return list(tools_by_name.values())

    async def _list_tools(self) -> Sequence[Tool]:
        return [
            tool
            for tool in await self.unfiltered_tools()
            if is_model_visible(tool) and not self._dedicated_app(tool)
        ]

    async def matching_tools(self, name: str, version=None) -> Sequence[Tool]:
        """保留所有实际候选，不能过滤 App/UI-only 后重新启用歧义名字。"""
        matches = []
        for server_name, info in self._eligible_servers():
            prefix = f"{server_name}{CATALOG_TOOL_SEPARATOR}"
            if not name.startswith(prefix):
                continue

            original_name = name[len(prefix) :]
            if not original_name:
                return []
            provider = FastMCPProvider(info["mcp"])
            tool = await provider.get_tool(original_name, version)
            if tool is not None:
                matches.append(
                    self._wrap_tool(server_name, info.get("config", {}), tool)
                )

        return matches

    async def _get_tool(self, name: str, version=None) -> Optional[Tool]:
        matches = await self.matching_tools(name, version)
        if len(matches) > 1:
            logger.error("Catalog 工具名冲突，拒绝调用: %s", name)
            return None
        return (
            matches[0]
            if matches
            and is_model_visible(matches[0])
            and not self._dedicated_app(matches[0])
            else None
        )
