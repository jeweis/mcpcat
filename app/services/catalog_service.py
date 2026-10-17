"""Catalog 服务，使用 FastMCP 官方 Search Transform。"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

from fastmcp import Client, FastMCP
from fastmcp.server.providers import FastMCPProvider
from fastmcp.server.transforms.search import RegexSearchTransform

from app.models.mcp_config import CatalogConfig
from app.services.app_mcp_cancellation import AppDisconnectMiddleware
from app.services.catalog_apps_provider import CatalogAppsProvider
from app.services.catalog_provider import CatalogProvider
from app.services.catalog_search import HybridBM25SearchTransform
from app.services.catalog_upstream_apps import CatalogUpstreamAppsProvider
from app.services.catalog_subscriptions import CatalogSubscriptions

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
        self._upstream_apps = CatalogUpstreamAppsProvider(
            server_manager, self._provider
        )
        self._mcp: Optional[FastMCP] = None
        self._mcp_app = None
        self._sse_app = None
        self._last_refresh = 0.0
        self._tool_count = 0
        self._lock = asyncio.Lock()
        self._initialized = False
        self.subscriptions = None

    def owns_server(self, server: FastMCP) -> bool:
        """用于通知持续验证当前 Catalog 代际，不复用旧实例身份。"""
        return self._initialized and self._mcp is server

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
            providers=[
                self._provider.wrap_transform(transform),
                CatalogAppsProvider(self._server_manager),
                self._upstream_apps,
            ],
        )
        runtime = getattr(self._server_manager, "skills_runtime", None)
        if runtime is not None:
            attachment = runtime.attach_catalog(self._mcp)
            self.subscriptions = CatalogSubscriptions(
                attachment, self._upstream_apps.sources
            )
            self._mcp.add_middleware(self.subscriptions)
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
        # 内置 Apps renderer 可达数 MiB，不能放进旧客户端默认 1 MiB SSE 帧。
        # 正式应用固定提供 Apps，HTTP 使用原生 JSON；旧 SSE 地址仍保留。
        apps_runtime = getattr(self._server_manager, "apps_runtime", None)
        use_json = apps_runtime is not None and apps_runtime.http_app is not None
        if use_json and self._mcp_app is None:
            self._mcp.add_middleware(AppDisconnectMiddleware())
        self._mcp_app = self._mcp.http_app(path="/", json_response=use_json)
        self._sse_app = self._mcp.http_app(path="/", transport="sse")
        return self._mcp_app, self._sse_app
