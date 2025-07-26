"""配置服务 - 封装配置加载逻辑"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Optional

from app.core.config import settings
from app.models.mcp_config import MCPConfig, create_config_from_dict

logger = logging.getLogger(__name__)


class ConfigService:
    """配置服务 - 负责加载和验证MCP服务器配置"""
    
    @staticmethod
    def load_raw_config() -> Dict[str, dict]:
        """
        加载原始配置文件 - 与现有的 load_config() 逻辑完全一致
        
        Returns:
            Dict[str, dict]: 原始配置字典
        """
        # 从config.py获取配置文件路径 - 与原逻辑一致
        config_path = settings.mcpcat_config_path
        print(f"配置文件路径: {config_path}")
        
        # 如果是相对路径，则相对于项目根目录 - 与原逻辑一致
        if not os.path.isabs(config_path):
            config_file = Path(__file__).parent.parent.parent / config_path
        else:
            config_file = Path(config_path)
        
        if config_file.exists():
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print(f"读取配置文件失败: {e}")
                return {}
        else:
            # 如果配置文件不存在，创建空配置文件 - 与原逻辑一致
            config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump({}, f, indent=2)
            print(f"已创建配置文件: {config_file}")
            return {}
    
    @staticmethod
    def load_validated_configs() -> Dict[str, MCPConfig]:
        """
        加载并验证MCP配置
        
        Returns:
            Dict[str, MCPConfig]: 验证后的配置字典
        """
        raw_configs = ConfigService.load_raw_config()
        validated_configs = {}
        
        for name, config_data in raw_configs.items():
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
    def load_config() -> Dict[str, dict]:
        """
        向后兼容的配置加载方法 - 与原有 load_config() 完全一致
        
        Returns:
            Dict[str, dict]: 原始配置字典
        """
        return ConfigService.load_raw_config()
    
    @staticmethod
    def save_config(config_dict: Dict[str, dict]) -> bool:
        """
        保存配置到文件
        
        Args:
            config_dict: 要保存的配置字典
            
        Returns:
            bool: 是否保存成功
        """
        try:
            # 从config.py获取配置文件路径
            config_path = settings.mcpcat_config_path
            
            # 如果是相对路径，则相对于项目根目录
            if not os.path.isabs(config_path):
                config_file = Path(__file__).parent.parent.parent / config_path
            else:
                config_file = Path(config_path)
            
            # 确保目录存在
            config_file.parent.mkdir(parents=True, exist_ok=True)
            
            # 保存配置文件
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(config_dict, f, indent=2, ensure_ascii=False)
            
            logger.info(f"✓ 配置已保存到: {config_file}")
            return True
            
        except Exception as e:
            logger.error(f"✗ 保存配置失败: {e}")
            return False
    
    @staticmethod
    def add_server_to_config(server_name: str, server_config: dict) -> bool:
        """
        添加服务器到配置文件
        
        Args:
            server_name: 服务器名称
            server_config: 服务器配置
            
        Returns:
            bool: 是否添加成功
        """
        try:
            # 加载现有配置
            current_config = ConfigService.load_raw_config()
            
            # 添加新服务器
            current_config[server_name] = server_config
            
            # 保存配置
            return ConfigService.save_config(current_config)
            
        except Exception as e:
            logger.error(f"✗ 添加服务器到配置失败: {e}")
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
            # 加载现有配置
            current_config = ConfigService.load_raw_config()
            
            # 检查服务器是否存在
            if server_name not in current_config:
                logger.error(f"服务器 {server_name} 不存在")
                return False
            
            # 更新服务器配置
            current_config[server_name] = new_config
            
            # 保存配置
            success = ConfigService.save_config(current_config)
            if success:
                logger.info(f"✓ 服务器 {server_name} 配置更新成功")
            return success
            
        except Exception as e:
            logger.error(f"✗ 更新服务器配置失败: {e}")
            return False
    
    @staticmethod  
    def remove_server_from_config(server_name: str) -> bool:
        """
        从配置文件中移除服务器
        
        Args:
            server_name: 服务器名称
            
        Returns:
            bool: 是否移除成功
        """
        try:
            # 加载现有配置
            current_config = ConfigService.load_raw_config()
            
            # 检查服务器是否存在
            if server_name in current_config:
                del current_config[server_name]
                success = ConfigService.save_config(current_config)
                if success:
                    logger.info(f"✓ 服务器 {server_name} 从配置中移除成功")
                return success
            else:
                # 服务器不存在也算成功
                logger.info(f"服务器 {server_name} 不存在于配置中")
                return True
                
        except Exception as e:
            logger.error(f"✗ 移除服务器配置失败: {e}")
            return False
    
    @staticmethod
    def validate_server_config(config: dict) -> tuple[bool, str]:
        """
        验证服务器配置的有效性
        
        Args:
            config: 服务器配置字典
            
        Returns:
            tuple[bool, str]: (是否有效, 错误信息)
        """
        try:
            # 使用 Pydantic 模型验证配置
            from app.models.mcp_config import create_config_from_dict
            create_config_from_dict(config)
            return True, ""
        except Exception as e:
            return False, str(e) 