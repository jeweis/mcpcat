"""MCP服务器工厂 - 封装服务器创建逻辑"""

import logging
from typing import Any, Dict, Optional

import httpx2
from fastmcp import FastMCP
from fastmcp.server.lifespan import ContextManagerLifespan
from fastmcp.server.providers.openapi import MCPType, OpenAPIProvider, RouteMap
from fastmcp.server.providers.proxy import FastMCPProxy

from app.services.proxy_clients import make_proxy_client_factory
from app.services.legacy_input_bridge import LegacyInputBridge
from app.services.modern_input_bridge import ModernInputBridge
from app.services.roots_notifications import RootsNotifications
from app.services.proxy_logging import ProxyLogging

logger = logging.getLogger(__name__)


class MCPServerFactory:
    """MCP服务器工厂类 - 封装现有的服务器创建逻辑"""

    @staticmethod
    def create_server(name: str, config_data: Dict[str, Any]) -> Optional[FastMCP]:
        """
        根据配置创建MCP服务器

        Args:
            name: 服务器名称
            config_data: 配置数据字典

        Returns:
            Optional[FastMCP]: 创建的MCP服务器实例，失败时返回None
        """
        try:
            mcp = None
            server_type = config_data.get("type")

            if server_type == "stdio":
                mcp = MCPServerFactory._create_stdio_server(config_data)
            elif server_type == "sse":
                mcp = MCPServerFactory._create_sse_server(config_data)
            elif server_type == "streamable-http":
                mcp = MCPServerFactory._create_streamable_http_server(config_data)
            elif server_type == "openapi":
                mcp = MCPServerFactory._create_openapi_server(config_data)
            else:
                logger.error(f"不支持的服务器类型: {server_type}")
                return None

            if mcp:
                if isinstance(mcp, FastMCPProxy):
                    mcp.add_middleware(LegacyInputBridge())
                logger.info(f"✓ MCP服务器 {name} 创建成功")
            else:
                logger.error(f"✗ MCP服务器 {name} 创建失败")

            return mcp

        except Exception as e:
            logger.error(f"创建MCP服务器 {name} 时发生异常: {e}")
            return None

    @staticmethod
    def _create_stdio_server(config_data: Dict[str, Any]) -> FastMCP:
        """创建STDIO类型的MCP服务器"""
        return MCPServerFactory._create_proxy_server(config_data)

    @staticmethod
    def _create_sse_server(config_data: Dict[str, Any]) -> FastMCP:
        """创建SSE类型的MCP服务器"""
        return MCPServerFactory._create_proxy_server(config_data)

    @staticmethod
    def _create_streamable_http_server(config_data: Dict[str, Any]) -> FastMCP:
        """创建Streamable HTTP类型的MCP服务器"""
        return MCPServerFactory._create_proxy_server(config_data)

    @staticmethod
    def _create_proxy_server(config_data: Dict[str, Any]) -> FastMCP:
        """代理输入等待随端点生命周期创建和关闭。"""
        bridge = ModernInputBridge()
        logging_bridge = ProxyLogging()
        server = FastMCPProxy(
            client_factory=make_proxy_client_factory(config_data),
            name="Config-Based Proxy",
            lifespan=ContextManagerLifespan(logging_bridge.lifespan)
            | ContextManagerLifespan(bridge.lifespan),
        )
        server.add_middleware(bridge)
        server.add_middleware(RootsNotifications())
        server.add_middleware(logging_bridge)
        return server

    @staticmethod
    def _create_openapi_server(config_data: Dict[str, Any]) -> FastMCP:
        """创建OpenAPI类型的MCP服务器"""
        client = httpx2.AsyncClient(base_url=config_data["api_base_url"])
        openapi_spec = httpx2.get(config_data["spec_url"]).json()
        route_map_list = []
        route_configs = config_data["route_configs"]

        for route_config in route_configs:
            route_map_list.append(
                RouteMap(
                    methods=route_config["methods"],
                    pattern=route_config["pattern"],
                    mcp_type=MCPType.TOOL,
                )
            )

        route_map_list.append(RouteMap(mcp_type=MCPType.EXCLUDE))

        provider = OpenAPIProvider(
            openapi_spec=openapi_spec,
            client=client,
            route_maps=route_map_list,
        )
        return FastMCP("openapi2mcpserver server", providers=[provider])
