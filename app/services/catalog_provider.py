"""Catalog 动态 Provider，聚合已纳入的 MCP 服务工具。"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from fastmcp.server.providers import FastMCPProvider, Provider
from fastmcp.tools import Tool

from app.models.mcp_config import CatalogConfig

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
        tools = await provider.list_tools()
        config = info.get("config", {})
        return [self._wrap_tool(server_name, config, tool) for tool in tools]

    async def _list_tools(self) -> Sequence[Tool]:
        servers = self._eligible_servers()
        results = await asyncio.gather(
            *(self._list_server_tools(name, info) for name, info in servers),
            return_exceptions=True,
        )

        tools_by_name: Dict[str, Tool] = {}
        collisions = set()
        for (server_name, _), result in zip(servers, results):
            if isinstance(result, BaseException):
                logger.warning("读取 MCP 服务 '%s' 工具失败: %s", server_name, result)
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

    async def _get_tool(self, name: str, version=None) -> Optional[Tool]:
        matches = []
        for server_name, info in self._eligible_servers():
            prefix = f"{server_name}{CATALOG_TOOL_SEPARATOR}"
            if not name.startswith(prefix):
                continue

            original_name = name[len(prefix) :]
            if not original_name:
                return None
            provider = FastMCPProvider(info["mcp"])
            tool = await provider.get_tool(original_name, version)
            if tool is not None:
                matches.append(
                    self._wrap_tool(server_name, info.get("config", {}), tool)
                )

        if len(matches) > 1:
            logger.error("Catalog 工具名冲突，拒绝调用: %s", name)
            return None
        return matches[0] if matches else None
