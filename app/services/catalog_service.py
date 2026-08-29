"""Catalog 服务，使用 FastMCP 官方 Search Transform。"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

from fastmcp import Client, FastMCP
from fastmcp.server.providers import FastMCPProvider
from fastmcp.server.transforms.search import RegexSearchTransform

from app.models.mcp_config import CatalogConfig
from app.services.catalog_provider import CatalogProvider
from app.services.catalog_search import HybridBM25SearchTransform

if TYPE_CHECKING:
    from app.services.server_manager import MCPServerManager

logger = logging.getLogger(__name__)

CATALOG_SERVER_NAME = "MCPCat Catalog"


class CatalogService:
    """全局工具搜索 MCP 服务。"""

    def __init__(
        self,
        server_manager: "MCPServerManager",
        config: CatalogConfig,
    ):
        self._server_manager = server_manager
        self._config = config
        self._provider = CatalogProvider(server_manager, config)
        self._mcp: Optional[FastMCP] = None
        self._mcp_app = None
        self._sse_app = None
        self._last_refresh = 0.0
        self._tool_count = 0
        self._lock = asyncio.Lock()
        self._initialized = False

    @property
    def tool_count(self) -> int:
        return self._tool_count

    @property
    def last_refresh(self) -> float:
        return self._last_refresh

    @property
    def mcp_app(self):
        return self._mcp_app

    @property
    def sse_app(self):
        return self._sse_app

    def _init_mcp(self) -> None:
        if self._initialized:
            return

        transform = (
            RegexSearchTransform(
                max_results=self._config.max_results,
                always_visible=self._config.always_visible,
            )
            if self._config.search_strategy == "regex"
            else HybridBM25SearchTransform(
                max_results=self._config.max_results,
                always_visible=self._config.always_visible,
            )
        )
        self._mcp = FastMCP(
            CATALOG_SERVER_NAME,
            providers=[self._provider],
            transforms=[transform],
        )
        self._initialized = True

    async def refresh_index(self) -> None:
        """读取实时 Provider 目录并更新状态统计。"""
        async with self._lock:
            tools = await self._provider.list_tools()
            self._tool_count = len(tools)
            self._last_refresh = time.time()
            logger.info("Catalog 目录刷新完成: %s 个工具", self._tool_count)

    async def search_tools(self, query: str) -> Any:
        """通过官方 synthetic search tool 执行 REST 搜索。"""
        self._init_mcp()
        argument_name = (
            "pattern" if self._config.search_strategy == "regex" else "query"
        )
        async with Client(self._mcp) as client:
            result = await client.call_tool(
                "search_tools",
                {argument_name: query},
            )
        return result.data

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """通过官方 synthetic call tool 执行已纳入的工具。"""
        self._init_mcp()
        async with Client(self._mcp) as client:
            result = await client.call_tool(
                "call_tool",
                {"name": name, "arguments": arguments},
            )
        if result.data is not None:
            return result.data

        content = []
        for block in result.content:
            text = getattr(block, "text", None)
            content.append(text if text is not None else str(block))
        return "\n".join(content)

    async def list_external_tools(self):
        """返回经 Search Transform 暴露给远程客户端的稳定工具契约。"""

        self._init_mcp()
        return await FastMCPProvider(self._mcp).list_tools()

    def get_catalog_status(self) -> Dict[str, Any]:
        return {
            "enabled": self._config.enabled,
            "tool_count": self._tool_count,
            "last_refresh": self._last_refresh,
            "last_refresh_iso": (
                time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(self._last_refresh),
                )
                if self._last_refresh
                else None
            ),
            "config": {
                "path_name": self._config.path_name,
                "require_auth": self._config.require_auth,
                "max_results": self._config.max_results,
                "search_strategy": self._config.search_strategy,
            },
        }

    def create_apps(self):
        self._init_mcp()
        self._mcp_app = self._mcp.http_app(path="/")
        self._sse_app = self._mcp.http_app(path="/", transport="sse")
        return self._mcp_app, self._sse_app
