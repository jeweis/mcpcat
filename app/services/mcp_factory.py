"""MCP服务器工厂 - 封装服务器创建逻辑"""

import logging
import httpx
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from fastmcp import FastMCP
from fastmcp.server import create_proxy
from fastmcp.server.providers.openapi import RouteMap, MCPType, OpenAPIProvider
from fastmcp.client.transports import StreamableHttpTransport, SSETransport
from fastmcp.client.auth import BearerAuth

from app.models.mcp_config import MCPConfig, StdioConfig, SSEConfig, StreamableHTTPConfig, OpenAPIConfig

logger = logging.getLogger(__name__)


def _get_oauth_token(config_data: Dict[str, Any]) -> Optional[str]:
    """从配置中提取有效的 OAuth access_token，过期则尝试刷新（同步检查，刷新需异步调用方处理）"""
    oauth = config_data.get('oauth')
    if not oauth or not oauth.get('token'):
        return None
    token_data = oauth['token']
    access_token = token_data.get('access_token')
    expires_at = token_data.get('expires_at')
    if not access_token:
        return None
    if expires_at:
        try:
            expires = datetime.fromisoformat(expires_at)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) >= expires:
                return None  # 过期，由调用方处理刷新
        except Exception:
            pass
    return access_token


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
            server_type = config_data.get('type')

            if server_type == 'stdio':
                mcp = MCPServerFactory._create_stdio_server(config_data)
            elif server_type == 'sse':
                mcp = MCPServerFactory._create_sse_server(config_data)
            elif server_type == 'streamable-http':
                mcp = MCPServerFactory._create_streamable_http_server(config_data)
            elif server_type == 'openapi':
                mcp = MCPServerFactory._create_openapi_server(config_data)
            else:
                logger.error(f"不支持的服务器类型: {server_type}")
                return None

            if mcp:
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
        env = config_data.get('env', {})
        mcp_config = {
            "mcpServers": {
                "default": {
                    "command": config_data['command'],
                    "args": config_data['args'],
                    "env": env
                }
            }
        }
        return create_proxy(mcp_config, name="Config-Based Proxy")

    @staticmethod
    def _create_sse_server(config_data: Dict[str, Any]) -> FastMCP:
        """创建SSE类型的MCP服务器"""
        headers = config_data.get('headers', {})
        url = config_data.get('url', "")

        # 如果有 OAuth token，使用 BearerAuth 认证连接
        token = _get_oauth_token(config_data)
        if token:
            transport = SSETransport(url=url, auth=BearerAuth(token=token), headers=headers)
            from fastmcp.server.providers.proxy import ProxyClient, FastMCPProxy
            client = ProxyClient(transport)
            return FastMCPProxy(client_factory=lambda: client, name="Config-Based Proxy")

        mcp_config = {
            "mcpServers": {
                "default": {
                    "url": url,
                    "transport": "sse",
                    "headers": headers
                }
            }
        }
        return create_proxy(mcp_config, name="Config-Based Proxy")

    @staticmethod
    def _create_streamable_http_server(config_data: Dict[str, Any]) -> FastMCP:
        """创建Streamable HTTP类型的MCP服务器"""
        headers = config_data.get('headers', {})
        url = config_data.get('url', "")

        # 如果有 OAuth token，使用 BearerAuth 认证连接
        token = _get_oauth_token(config_data)
        if token:
            transport = StreamableHttpTransport(url=url, auth=BearerAuth(token=token), headers=headers)
            from fastmcp.server.providers.proxy import ProxyClient, FastMCPProxy
            client = ProxyClient(transport)
            return FastMCPProxy(client_factory=lambda: client, name="Config-Based Proxy")

        mcp_config = {
            "mcpServers": {
                "default": {
                    "url": url,
                    "transport": "streamable-http",
                    "headers": headers
                }
            }
        }
        return create_proxy(mcp_config, name="Config-Based Proxy")

    @staticmethod
    def _create_openapi_server(config_data: Dict[str, Any]) -> FastMCP:
        """创建OpenAPI类型的MCP服务器"""
        client = httpx.AsyncClient(base_url=config_data['api_base_url'])
        openapi_spec = httpx.get(config_data["spec_url"]).json()
        route_map_list = []
        route_configs = config_data["route_configs"]

        for route_config in route_configs:
            route_map_list.append(RouteMap(
                methods=route_config['methods'],
                pattern=route_config['pattern'],
                mcp_type=MCPType.TOOL,
            ))

        route_map_list.append(RouteMap(mcp_type=MCPType.EXCLUDE))

        provider = OpenAPIProvider(
            openapi_spec=openapi_spec,
            client=client,
            route_maps=route_map_list,
        )
        return FastMCP("openapi2mcpserver server", providers=[provider])