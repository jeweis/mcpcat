"""配置服务 - 封装配置加载逻辑"""

import logging
from copy import deepcopy
from typing import Any, Dict, List, Optional

from app.models.mcp_config import MCPConfig, create_config_from_dict
from app.storage.database import Database
from app.storage.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


class ConfigService:
    """从 SQLite 重建旧格式配置的兼容 facade。"""

    _database: Optional[Database] = None

    @classmethod
    def configure_database(cls, database: Database) -> None:
        """绑定已经完成 Schema 升级和旧数据迁移的数据库。"""

        cls._database = database

    @classmethod
    def reset_database(cls) -> None:
        """解除数据库绑定，仅供应用关闭和隔离测试使用。"""

        cls._database = None

    @classmethod
    def _require_database(cls) -> Database:
        if cls._database is None:
            raise RuntimeError("ConfigService 尚未完成存储初始化")
        return cls._database

    @staticmethod
    def load_raw_config() -> Dict:
        """从 Repository 重建完整的旧格式配置字典。"""

        with UnitOfWork(ConfigService._require_database()) as unit:
            config = unit.settings.list_all()
            security = deepcopy(config.get("security") or {})
            security["api_keys"] = unit.api_keys.list_all()
            config["security"] = security
            config["mcpServers"] = unit.mcp_servers.list_configs()
            return config

    @staticmethod
    def _create_default_config() -> Dict:
        """创建默认配置"""
        return {
            "mcpServers": {},
            "security": {"api_keys": [], "auth_header_name": "Mcpcat-Key"},
            "app": {"version": "0.1.1", "log_level": "INFO", "enable_metrics": True},
        }

    @staticmethod
    def load_validated_configs() -> Dict[str, MCPConfig]:
        """
        加载并验证MCP配置

        Returns:
            Dict[str, MCPConfig]: 验证后的配置字典
        """
        full_config = ConfigService.load_raw_config()
        mcp_servers = full_config.get("mcpServers", {})
        validated_configs = {}

        for name, config_data in mcp_servers.items():
            try:
                # 使用 Pydantic 验证配置
                validated_config = create_config_from_dict(config_data)
                validated_configs[name] = validated_config
                logger.info(f"✓ 配置验证成功: {name}")
            except Exception as e:
                logger.error(f"✗ 配置验证失败 {name}: {e}")
                # 继续处理其他配置，不中断
                continue

        return validated_configs

    @staticmethod
    def load_config() -> Dict:
        """
        向后兼容的配置加载方法，现在返回完整配置

        Returns:
            Dict: 完整配置字典
        """
        return ConfigService.load_raw_config()

    @staticmethod
    def load_mcp_servers_config() -> Dict[str, dict]:
        """
        加载MCP服务器配置（向后兼容）

        Returns:
            Dict[str, dict]: MCP服务器配置字典
        """
        with UnitOfWork(ConfigService._require_database()) as unit:
            return unit.mcp_servers.list_configs()

    @staticmethod
    def get_server_config(server_name: str) -> Optional[Dict[str, Any]]:
        """通过 MCP Repository 获取一个服务配置。"""

        with UnitOfWork(ConfigService._require_database()) as unit:
            return unit.mcp_servers.get_config(server_name)

    @staticmethod
    def save_config(config_dict: Dict) -> bool:
        """在单事务中将兼容字典按实体差异写入 SQLite。"""

        if not isinstance(config_dict, dict):
            logger.error("✗ 保存配置失败: 配置根节点必须是对象")
            return False
        try:
            mcp_servers = config_dict.get("mcpServers") or {}
            security = deepcopy(config_dict.get("security") or {})
            api_keys = security.pop("api_keys", [])
            if not isinstance(mcp_servers, dict) or not isinstance(api_keys, list):
                raise ValueError("mcpServers 必须是对象且 security.api_keys 必须是数组")

            with UnitOfWork(ConfigService._require_database()) as unit:
                existing_servers = set(unit.mcp_servers.list_configs())
                for name, value in mcp_servers.items():
                    if not isinstance(value, dict):
                        raise ValueError(f"MCP 服务 {name} 配置必须是对象")
                    unit.mcp_servers.upsert(str(name), value)
                for name in existing_servers - set(mcp_servers):
                    unit.mcp_servers.remove(name)

                unit.api_keys.replace_all(api_keys)

                settings_payload = {
                    key: deepcopy(value)
                    for key, value in config_dict.items()
                    if key not in {"mcpServers", "security"}
                }
                settings_payload["security"] = security
                existing_sections = set(unit.settings.list_all())
                for section, value in settings_payload.items():
                    unit.settings.set(section, value)
                for section in existing_sections - set(settings_payload):
                    unit.settings.remove(section)
                unit.commit()

            logger.info("✓ 配置已保存到 SQLite")
            return True
        except Exception as e:
            logger.error("✗ 保存配置到 SQLite 失败: %s", type(e).__name__)
            return False

    @staticmethod
    def add_server_to_config(server_name: str, server_config: dict) -> bool:
        """通过 MCP Repository 新增或替换服务器。"""

        try:
            with UnitOfWork(ConfigService._require_database()) as unit:
                unit.mcp_servers.upsert(server_name, server_config)
                unit.commit()
            return True
        except Exception as e:
            logger.error("✗ 添加服务器到数据库失败: %s", type(e).__name__)
            return False

    @staticmethod
    def update_server_config(server_name: str, new_config: dict) -> bool:
        """
        更新指定服务器的配置

        Args:
            server_name: 服务器名称
            new_config: 新的服务器配置

        Returns:
            bool: 是否更新成功
        """
        try:
            with UnitOfWork(ConfigService._require_database()) as unit:
                if unit.mcp_servers.get_config(server_name) is None:
                    logger.error("服务器 %s 不存在", server_name)
                    return False
                unit.mcp_servers.upsert(server_name, new_config)
                unit.commit()
            logger.info("✓ 服务器 %s 配置更新成功", server_name)
            return True
        except Exception as e:
            logger.error("✗ 更新服务器配置失败: %s", type(e).__name__)
            return False

    @staticmethod
    def update_server_meta(
        server_name: str, note: Optional[str] = None, tags: Optional[List[str]] = None
    ) -> bool:
        """仅更新服务器备注或标签。"""

        try:
            with UnitOfWork(ConfigService._require_database()) as unit:
                config = unit.mcp_servers.get_config(server_name)
                if config is None:
                    logger.error("服务器 %s 不存在", server_name)
                    return False
                if note is not None:
                    config["note"] = note
                if tags is not None:
                    config["tags"] = tags
                unit.mcp_servers.upsert(server_name, config)
                unit.commit()
            return True
        except Exception as e:
            logger.error("✗ 更新服务器元数据失败: %s", type(e).__name__)
            return False

    @staticmethod
    def update_server_oauth(server_name: str, oauth_config: Any) -> bool:
        """通过 MCP Repository 持久化 OAuth 配置和 Token。"""

        try:
            with UnitOfWork(ConfigService._require_database()) as unit:
                config = unit.mcp_servers.get_config(server_name)
                if config is None:
                    logger.error("服务器 %s 不存在", server_name)
                    return False
                if hasattr(oauth_config, "model_dump"):
                    oauth_dict = oauth_config.model_dump(exclude_none=True)
                elif hasattr(oauth_config, "dict"):
                    oauth_dict = oauth_config.dict(exclude_none=True)
                else:
                    oauth_dict = deepcopy(oauth_config)
                config["oauth"] = oauth_dict
                unit.mcp_servers.upsert(server_name, config)
                unit.commit()
            return True
        except Exception as e:
            logger.error("✗ 更新服务器 OAuth 配置失败: %s", type(e).__name__)
            return False

    @staticmethod
    def get_public_base_url() -> Optional[str]:
        """从 Settings Repository 获取全局 public_base_url。"""

        try:
            with UnitOfWork(ConfigService._require_database()) as unit:
                app_config = unit.settings.get("app", {}) or {}
            return app_config.get("public_base_url") or None
        except Exception:
            return None

    @staticmethod
    def remove_server_from_config(server_name: str) -> bool:
        """通过 MCP Repository 删除服务器。"""

        try:
            with UnitOfWork(ConfigService._require_database()) as unit:
                unit.mcp_servers.remove(server_name)
                unit.commit()
            return True
        except Exception as e:
            logger.error("✗ 移除服务器配置失败: %s", type(e).__name__)
            return False

    @staticmethod
    def update_setting_section(section: str, value: Any) -> bool:
        """通过 Settings Repository 原子更新一个顶层设置段。"""

        try:
            with UnitOfWork(ConfigService._require_database()) as unit:
                unit.settings.set(section, value)
                unit.commit()
            return True
        except Exception as e:
            logger.error("✗ 更新设置段失败: %s", type(e).__name__)
            return False

    @staticmethod
    def get_setting_section(section: str, default: Any = None) -> Any:
        """读取一个顶层设置段。"""

        with UnitOfWork(ConfigService._require_database()) as unit:
            return unit.settings.get(section, default)

    @staticmethod
    def validate_server_config(config: dict) -> tuple[bool, str]:
        """验证服务器配置。"""

        try:
            create_config_from_dict(config)
            return True, ""
        except Exception as e:
            return False, str(e)
