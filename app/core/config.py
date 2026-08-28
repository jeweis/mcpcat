"""应用配置管理"""

import os
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用设置"""

    # 应用基础配置
    app_name: str = "mcpcat"
    app_version: str = "0.1.1"
    description: str = "MCP聚合平台 - 支持多种MCP协议的统一管理平台"

    # 服务器配置
    host: str = "0.0.0.0"
    port: int = 8000

    # MCP配置文件路径
    mcpcat_config_path: str = ".mcpcat/config.json"

    # 持久化与 Skill 制品路径
    mcpcat_database_path: str = ".mcpcat/mcpcat.db"
    mcpcat_artifacts_path: str = ".mcpcat/skills/artifacts"
    mcpcat_backups_path: str = ".mcpcat/backups"
    mcpcat_sqlite_busy_timeout_ms: int = 5000
    mcpcat_legacy_migration_lock_timeout_sec: int = 30

    # Skill ZIP 安全限制
    mcpcat_skill_zip_max_bytes: int = 20 * 1024 * 1024
    mcpcat_skill_expanded_max_bytes: int = 100 * 1024 * 1024
    mcpcat_skill_file_max_bytes: int = 10 * 1024 * 1024
    mcpcat_skill_max_files: int = 1000
    mcpcat_mcp_skill_max_tools: int = 500
    mcpcat_skills_enabled: bool = True

    # 日志配置
    log_level: str = "INFO"

    # 默认 API Key 配置（可选，不设置则自动生成随机值）
    mcpcat_default_admin_key: Optional[str] = None
    mcpcat_default_read_key: Optional[str] = None

    # 全局加密密钥（可选，用于加密飞书 app_secret 等敏感配置；不设置则自动生成并持久化）
    mcpcat_secret_key: Optional[str] = None

    class Config:
        env_file = ".env"
        case_sensitive = False


# 全局设置实例
settings = Settings()
