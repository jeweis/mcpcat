"""Apps 配置与系统路径保留，不改变已有服务地址。"""

from typing import Any

from app.models.apps_mcp_config import AppsMCPConfig
from app.services.config_service import ConfigService


def apps_mcp_config() -> AppsMCPConfig:
    """缺失配置使用需认证的默认入口，不回写配置原文。"""
    return AppsMCPConfig.model_validate(
        ConfigService.get_setting_section("apps", {}) or {}
    )


def validate_apps_path(config: AppsMCPConfig, manager: Any) -> None:
    """保留普通服务、Catalog、Skills、CodeMode 的独立路由。"""
    from app.services.platform_endpoints import validate_platform_path

    validate_platform_path("apps", config.path_name, manager)


def apps_path_reserved(name: str) -> bool:
    """启用后等待挂载的路径也不交给普通用户服务。"""
    config = apps_mcp_config()
    return name == config.path_name
