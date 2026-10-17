"""固定系统入口的旧实例路径兼容，不占用已有 MCP 地址。"""

import logging
from typing import Any

from app.models.apps_mcp_config import AppsMCPConfig
from app.models.code_mode_config import CodeModeConfig
from app.models.skills_mcp_config import SkillsMCPConfig
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)
ENDPOINT_MODELS = {
    "skills_mcp": SkillsMCPConfig,
    "apps": AppsMCPConfig,
    "code_mode": CodeModeConfig,
}


def prepare_platform_paths(manager: Any) -> None:
    """启动时保留旧服务，冲突系统路径避让并持久化，重启不漂移。"""
    raw = {
        section: ConfigService.get_setting_section(section, {}) or {}
        for section in ENDPOINT_MODELS
    }
    configs = {
        section: model.model_validate(raw[section])
        for section, model in ENDPOINT_MODELS.items()
    }
    occupied = set(ConfigService.load_mcp_servers_config())
    occupied.update(getattr(manager, "server_info", {}))
    catalog = ConfigService.get_setting_section("catalog", {}) or {}
    # 即使旧 Catalog 暂停也保留其历史地址，避免未来重新启用时被占用。
    occupied.add(catalog.get("path_name", "mcpcat"))
    requested = {config.path_name for config in configs.values()}
    # 显式保存的旧系统地址优先于新默认值，避免新增入口抢走旧连接。
    order = sorted(configs, key=lambda section: "path_name" not in raw[section])
    for section in order:
        config = configs[section]
        name = config.path_name
        if name in occupied:
            base = "mcpcat-" + ENDPOINT_MODELS[section]().path_name
            name, index = base, 2
            while name in occupied or name in requested:
                name, index = f"{base}-{index}", index + 1
            original = ConfigService.get_setting_section(section, {}) or {}
            if not ConfigService.update_setting_section(
                section, {**original, "path_name": name}
            ):
                raise RuntimeError("无法保存系统入口避让地址")
            logger.warning("系统入口 %s 路径冲突，使用 /mcp/%s", section, name)
        occupied.add(name)


def validate_platform_path(section: str, name: str, manager: Any) -> None:
    """用户明确修改地址时严格报冲突，不悄悄改成另一个地址。"""
    if name in ConfigService.load_mcp_servers_config() or name in getattr(
        manager, "server_info", {}
    ):
        raise ValueError(f"系统路径 '{name}' 与已有 MCP 服务冲突")
    catalog = ConfigService.get_setting_section("catalog", {}) or {}
    if name == catalog.get("path_name", "mcpcat"):
        raise ValueError(f"系统路径 '{name}' 与 catalog 冲突")
    for other, model in ENDPOINT_MODELS.items():
        if other != section:
            config = model.model_validate(
                ConfigService.get_setting_section(other, {}) or {}
            )
            if name == config.path_name:
                raise ValueError(f"系统路径 '{name}' 与 {other} 冲突")


def skills_path_reserved(name: str) -> bool:
    """已保存但待重启的 Skills 地址也不能交给用户服务。"""
    config = SkillsMCPConfig.model_validate(
        ConfigService.get_setting_section("skills_mcp", {}) or {}
    )
    return name == config.path_name
