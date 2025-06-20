"""MCP服务器配置模型"""

from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Union, Literal
from enum import Enum


class MCPTransportType(str, Enum):
    """MCP传输类型枚举"""
    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable-http"
    OPENAPI = "openapi"


class MCPBaseConfig(BaseModel):
    """MCP服务器基础配置"""
    type: MCPTransportType
    name: Optional[str] = None
    enabled: bool = True
    timeout: int = Field(default=30, ge=1, le=300)
    
    class Config:
        extra = "allow"  # 允许额外字段，保持兼容性


class StdioConfig(MCPBaseConfig):
    """STDIO传输配置 - 对应现有的 stdio 类型"""
    type: Literal[MCPTransportType.STDIO]
    command: str
    args: List[str] = []
    env: Dict[str, str] = {}


class SSEConfig(MCPBaseConfig):
    """SSE传输配置 - 对应现有的 sse 类型"""
    type: Literal[MCPTransportType.SSE]
    url: str
    headers: Dict[str, str] = {}


class StreamableHTTPConfig(MCPBaseConfig):
    """Streamable HTTP传输配置 - 对应现有的 streamable-http 类型"""
    type: Literal[MCPTransportType.STREAMABLE_HTTP]
    url: str
    headers: Dict[str, str] = {}


class RouteConfig(BaseModel):
    """OpenAPI路由配置"""
    methods: List[str]
    pattern: str


class OpenAPIConfig(MCPBaseConfig):
    """OpenAPI配置 - 对应现有的 openapi 类型"""
    type: Literal[MCPTransportType.OPENAPI]
    spec_url: str
    api_base_url: str
    route_configs: List[RouteConfig]


# 联合类型，对应所有可能的配置
MCPConfig = Union[StdioConfig, SSEConfig, StreamableHTTPConfig, OpenAPIConfig]


def create_config_from_dict(config_data: dict) -> MCPConfig:
    """
    从字典创建配置对象 - 保持与现有逻辑完全兼容
    
    Args:
        config_data: 配置字典
        
    Returns:
        MCPConfig: 对应的配置对象
        
    Raises:
        ValueError: 不支持的配置类型
    """
    config_type = config_data.get('type')
    
    if config_type == 'stdio':
        return StdioConfig(**config_data)
    elif config_type == 'sse':
        return SSEConfig(**config_data)
    elif config_type == 'streamable-http':
        return StreamableHTTPConfig(**config_data)
    elif config_type == 'openapi':
        # 处理 route_configs
        route_configs = config_data.get('route_configs', [])
        processed_routes = [RouteConfig(**route) for route in route_configs]
        config_data = config_data.copy()
        config_data['route_configs'] = processed_routes
        return OpenAPIConfig(**config_data)
    else:
        raise ValueError(f"不支持的配置类型: {config_type}")


def config_to_dict(config: MCPConfig) -> dict:
    """
    将配置对象转换回字典 - 确保与现有逻辑兼容
    
    Args:
        config: MCP配置对象
        
    Returns:
        dict: 配置字典
    """
    if isinstance(config, OpenAPIConfig):
        # 特殊处理 route_configs
        result = config.dict()
        result['route_configs'] = [route.dict() for route in config.route_configs]
        return result
    else:
        return config.dict() 