"""安全服务 - 管理API Key和权限验证"""

import secrets
import string
from typing import Optional, List, Dict
from datetime import datetime
from app.models.mcp_config import APIKeyConfig, PermissionType, SecurityConfig
from app.services.config_service import ConfigService
import logging

logger = logging.getLogger(__name__)


class SecurityService:
    """安全服务类"""
    
    def __init__(self):
        self._config_service = ConfigService()
    
    def get_auth_header_name(self) -> str:
        """
        获取认证头名称
        
        Returns:
            str: 认证头名称
        """
        try:
            config = self._config_service.load_config()
            security_config = config.get('security', {})
            return security_config.get('auth_header_name', 'Mcpcat-Key')
        except Exception as e:
            logger.error(f"获取认证头名称时出错: {e}")
            return 'Mcpcat-Key'  # 默认值
    
    def _process_datetime_fields(self, key_data: dict) -> dict:
        """
        处理datetime字段的序列化和反序列化
        
        Args:
            key_data: API Key数据字典
            
        Returns:
            dict: 处理后的数据字典
        """
        processed_data = key_data.copy()
        
        # 处理created_at字段
        if processed_data.get('created_at'):
            if isinstance(processed_data['created_at'], str):
                try:
                    processed_data['created_at'] = datetime.fromisoformat(processed_data['created_at'])
                except:
                    processed_data['created_at'] = None
        
        # 处理expires_at字段
        if processed_data.get('expires_at'):
            if isinstance(processed_data['expires_at'], str):
                try:
                    processed_data['expires_at'] = datetime.fromisoformat(processed_data['expires_at'])
                except:
                    processed_data['expires_at'] = None
        
        return processed_data
    
    def generate_api_key(self, length: int = 32) -> str:
        """
        生成安全的API Key
        
        Args:
            length: API Key长度
            
        Returns:
            str: 生成的API Key
        """
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(length))
    
    def verify_api_key(self, api_key: str) -> Optional[APIKeyConfig]:
        """
        验证API Key
        
        Args:
            api_key: 要验证的API Key
            
        Returns:
            APIKeyConfig: 如果验证成功返回API Key配置，否则返回None
        """
        if not api_key or not api_key.strip():
            return None
            
        try:
            config = self._config_service.load_config()
            security_config = config.get('security', {})
            api_keys = security_config.get('api_keys', [])
            
            for key_data in api_keys:
                processed_data = self._process_datetime_fields(key_data)
                key_config = APIKeyConfig(**processed_data)
                
                # 检查Key是否匹配且启用
                if key_config.key == api_key.strip() and key_config.enabled:
                    # 检查是否过期
                    if key_config.expires_at and datetime.now() > key_config.expires_at:
                        logger.warning(f"API Key已过期: {key_config.name}")
                        return None
                    
                    return key_config
            
            return None
            
        except Exception as e:
            logger.error(f"验证API Key时出错: {e}")
            return None
    
    def has_permission(self, api_key_config: APIKeyConfig, required_permission: PermissionType) -> bool:
        """
        检查API Key是否有指定权限
        
        Args:
            api_key_config: API Key配置
            required_permission: 需要的权限
            
        Returns:
            bool: 是否有权限
        """
        if not api_key_config or not api_key_config.enabled:
            return False
        
        # write权限包含read权限
        if api_key_config.permission == PermissionType.WRITE:
            return True
        
        # 检查具体权限
        return api_key_config.permission == required_permission
    
    def get_all_api_keys(self) -> List[APIKeyConfig]:
        """
        获取所有API Key配置
        
        Returns:
            List[APIKeyConfig]: API Key配置列表
        """
        try:
            config = self._config_service.load_config()
            security_config = config.get('security', {})
            api_keys = security_config.get('api_keys', [])
            
            return [APIKeyConfig(**self._process_datetime_fields(key_data)) for key_data in api_keys]
            
        except Exception as e:
            logger.error(f"获取API Key列表时出错: {e}")
            return []
    
    def add_api_key(self, name: str, permission: PermissionType, 
                   key: Optional[str] = None, expires_at: Optional[datetime] = None) -> APIKeyConfig:
        """
        添加新的API Key
        
        Args:
            name: API Key名称
            permission: 权限级别
            key: 指定的Key，如果为None则自动生成
            expires_at: 过期时间
            
        Returns:
            APIKeyConfig: 创建的API Key配置
            
        Raises:
            ValueError: 如果Key已存在或配置无效
        """
        if not key:
            key = self.generate_api_key()
        
        # 检查Key是否已存在
        if self.verify_api_key(key):
            raise ValueError(f"API Key已存在")
        
        new_key = APIKeyConfig(
            key=key,
            name=name,
            permission=permission,
            enabled=True,
            created_at=datetime.now(),
            expires_at=expires_at
        )
        
        # 加载当前配置
        config = self._config_service.load_config()
        
        # 确保security配置存在
        if 'security' not in config:
            config['security'] = {
                'api_keys': [],
                'auth_header_name': 'Mcpcat-Key'
            }
        if 'api_keys' not in config['security']:
            config['security']['api_keys'] = []
        if 'auth_header_name' not in config['security']:
            config['security']['auth_header_name'] = 'Mcpcat-Key'
        
        # 添加新Key（处理datetime序列化）
        key_dict = new_key.dict()
        if key_dict.get('created_at'):
            key_dict['created_at'] = key_dict['created_at'].isoformat()
        if key_dict.get('expires_at'):
            key_dict['expires_at'] = key_dict['expires_at'].isoformat()
        
        config['security']['api_keys'].append(key_dict)
        
        # 保存配置
        self._config_service.save_config(config)
        
        logger.info(f"添加新API Key: {name} ({permission.value})")
        return new_key
    
    def remove_api_key(self, key: str) -> bool:
        """
        删除API Key
        
        Args:
            key: 要删除的API Key
            
        Returns:
            bool: 是否删除成功
        """
        try:
            config = self._config_service.load_config()
            security_config = config.get('security', {})
            api_keys = security_config.get('api_keys', [])
            
            # 查找并删除Key
            original_count = len(api_keys)
            api_keys[:] = [k for k in api_keys if k.get('key') != key]
            
            if len(api_keys) < original_count:
                config['security']['api_keys'] = api_keys
                self._config_service.save_config(config)
                logger.info(f"删除API Key: {key[:8]}...")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"删除API Key时出错: {e}")
            return False
    
    def update_api_key(self, key: str, **updates) -> bool:
        """
        更新API Key配置
        
        Args:
            key: 要更新的API Key
            **updates: 要更新的字段
            
        Returns:
            bool: 是否更新成功
        """
        try:
            config = self._config_service.load_config()
            security_config = config.get('security', {})
            api_keys = security_config.get('api_keys', [])
            
            # 查找并更新Key
            for key_data in api_keys:
                if key_data.get('key') == key:
                    key_data.update(updates)
                    config['security']['api_keys'] = api_keys
                    self._config_service.save_config(config)
                    logger.info(f"更新API Key: {key[:8]}...")
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"更新API Key时出错: {e}")
            return False
    
    def ensure_default_keys(self) -> List[APIKeyConfig]:
        """
        确保存在默认的API Key，如果不存在则创建
        
        Returns:
            List[APIKeyConfig]: 创建的默认Key列表
        """
        existing_keys = self.get_all_api_keys()
        
        # 如果已有Key，不创建默认Key
        if existing_keys:
            return []
        
        created_keys = []
        
        try:
            # 创建默认的write权限Key
            admin_key = self.add_api_key(
                name="Default Admin Key",
                permission=PermissionType.WRITE
            )
            created_keys.append(admin_key)
            
            # 创建默认的read权限Key
            read_key = self.add_api_key(
                name="Default Read Key", 
                permission=PermissionType.READ
            )
            created_keys.append(read_key)
            
            logger.info("已创建默认API Key")
            
        except Exception as e:
            logger.error(f"创建默认API Key时出错: {e}")
        
        return created_keys


# 全局安全服务实例
security_service = SecurityService()