"""应用配置管理"""

import os
from typing import List, Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用设置"""
    
    # 应用基础配置
    app_name: str = "MCPCat"
    app_version: str = "0.1.0"
    debug: bool = False
    
    # 服务器配置
    host: str = "0.0.0.0"
    port: int = 8000
    
    # MCP配置
    mcp_default_protocol: str = "stdio"
    mcp_max_connections: int = 100
    mcp_timeout: int = 30
    
    # 日志配置
    log_level: str = "INFO"
    log_file: str = "logs/mcpcat.log"
    
    # 安全配置
    secret_key: str = "your-secret-key-here"
    access_token_expire_minutes: int = 30
    
    # CORS配置
    allowed_origins: List[str] = [
        "http://localhost:3000",
        "http://localhost:8080"
    ]
    
    # 数据库配置（可选）
    database_url: Optional[str] = None
    
    # MCP配置文件路径
    mcpcat_config_path: str = "config.json"
    
    class Config:
        env_file = ".env"
        case_sensitive = False


# 全局设置实例
settings = Settings()