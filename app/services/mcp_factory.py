"""MCP服务器工厂 - 封装服务器创建逻辑"""

import logging
import httpx
from typing import Optional, Dict, Any
from fastmcp import FastMCP
from fastmcp.server.openapi import RouteMap, MCPType

from app.models.mcp_config import MCPConfig, StdioConfig, SSEConfig, StreamableHTTPConfig, OpenAPIConfig

logger = logging.getLogger(__name__)


class MCPServerFactory:
    """MCP服务器工厂类 - 封装现有的服务器创建逻辑"""
    
    @staticmethod
    def create_server(name: str, config_data: Dict[str, Any]) -> Optional[FastMCP]:
        """
        根据配置创建MCP服务器 - 与现有的 add_mcp_server 逻辑完全一致
        
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
        """
        创建STDIO类型的MCP服务器 - 与原逻辑完全一致
        
        Args:
            config_data: 配置数据
            
        Returns:
            FastMCP: MCP服务器实例
        """
        # 取value的env值，没有值时为空 - 与原逻辑一致
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
        return FastMCP.as_proxy(mcp_config, name="Config-Based Proxy")
    
    @staticmethod
    def _create_sse_server(config_data: Dict[str, Any]) -> FastMCP:
        """
        创建SSE类型的MCP服务器 - 与原逻辑完全一致
        
        Args:
            config_data: 配置数据
            
        Returns:
            FastMCP: MCP服务器实例
        """
        headers = config_data.get('headers', {})
        url = config_data.get('url', "")
        mcp_config = {
            "mcpServers": {
                "default": {
                    "url": url,
                    "transport": "sse",
                    "headers": headers
                }
            }
        }
        return FastMCP.as_proxy(mcp_config, name="Config-Based Proxy")
    
    @staticmethod
    def _create_streamable_http_server(config_data: Dict[str, Any]) -> FastMCP:
        """
        创建Streamable HTTP类型的MCP服务器 - 与原逻辑完全一致
        
        Args:
            config_data: 配置数据
            
        Returns:
            FastMCP: MCP服务器实例
        """
        headers = config_data.get('headers', {})
        url = config_data.get('url', "")
        mcp_config = {
            "mcpServers": {
                "default": {
                    "url": url,
                    "transport": "streamable-http",
                    "headers": headers
                }
            }
        }
        return FastMCP.as_proxy(mcp_config, name="Config-Based Proxy")
    
    @staticmethod
    def _create_openapi_server(config_data: Dict[str, Any]) -> FastMCP:
        """
        创建OpenAPI类型的MCP服务器 - 与原逻辑完全一致
        
        Args:
            config_data: 配置数据
            
        Returns:
            FastMCP: MCP服务器实例
        """
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
        
        # 添加默认的排除规则 - 与原逻辑一致
        route_map_list.append(RouteMap(mcp_type=MCPType.EXCLUDE))
        
        return FastMCP.from_openapi(
            openapi_spec=openapi_spec,
            client=client,
            name="openapi2mcpserver server",
            route_maps=route_map_list
        ) 