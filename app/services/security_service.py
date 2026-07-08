"""安全服务 - 管理API Key和权限验证"""

import base64
import hashlib
import hmac
import os
import secrets
import string
import threading
from typing import Optional, List, Dict, Tuple
from datetime import datetime
from app.models.mcp_config import APIKeyConfig, PermissionType, SecurityConfig, FeishuConfig
from app.services.config_service import ConfigService
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


class SecurityService:
    """安全服务类"""

    def __init__(self):
        self._config_service = ConfigService()
        # 临时存储首次生成的 Key（仅展示一次）
        self._first_run_keys: Optional[dict] = None
        # 飞书首次登录"查找-创建"过程的进程内锁，防止并发写入冲突
        self._feishu_login_lock = threading.Lock()
        # 全局加密密钥的内存缓存与解析锁（避免重复读写配置文件、互相覆盖）
        self._secret_key_cache: Optional[bytes] = None
        self._secret_key_lock = threading.Lock()

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
            # 检查是否通过环境变量设置了 Key
            admin_key_from_env = settings.mcpcat_default_admin_key is not None
            read_key_from_env = settings.mcpcat_default_read_key is not None

            # 创建默认的write权限Key（优先使用环境变量配置）
            admin_key = self.add_api_key(
                name="Default Admin Key",
                permission=PermissionType.WRITE,
                key=settings.mcpcat_default_admin_key  # None 时自动生成
            )
            created_keys.append(admin_key)

            # 创建默认的read权限Key（优先使用环境变量配置）
            read_key = self.add_api_key(
                name="Default Read Key",
                permission=PermissionType.READ,
                key=settings.mcpcat_default_read_key  # None 时自动生成
            )
            created_keys.append(read_key)

            # 如果是自动生成的 Key（非环境变量设置），保存用于首次展示
            if not admin_key_from_env or not read_key_from_env:
                self._first_run_keys = {
                    'admin_key': admin_key.key if not admin_key_from_env else None,
                    'read_key': read_key.key if not read_key_from_env else None,
                    'admin_key_name': admin_key.name,
                    'read_key_name': read_key.name
                }

            logger.info("已创建默认API Key")

        except Exception as e:
            logger.error(f"创建默认API Key时出错: {e}")

        return created_keys

    def get_first_run_keys(self) -> Optional[dict]:
        """
        获取首次运行时生成的 Key（仅返回一次）

        Returns:
            Optional[dict]: 首次生成的 Key 信息，如果没有或已获取过则返回 None
        """
        return self._first_run_keys

    def clear_first_run_keys(self) -> None:
        """清除首次运行的 Key 信息（获取后调用）"""
        self._first_run_keys = None

    # ------------------------------------------------------------------
    # 全局加密密钥与 app_secret 加解密
    # ------------------------------------------------------------------

    def _resolve_global_secret_key(self) -> bytes:
        """
        解析全局加密密钥（解析结果会缓存在内存中，避免重复读写配置文件）

        优先级：环境变量 MCPCAT_SECRET_KEY > config.json 中持久化的 app.secret_key，
        都没有时自动生成一个随机值并持久化（仅起到混淆作用，不是真正的安全边界）。

        注意：本方法可能触发一次独立的"读取-修改-保存"配置文件操作，因此调用方若
        随后还要整体保存配置，必须先调用本方法完成密钥解析/持久化，避免互相覆盖。

        Returns:
            bytes: 加密密钥的字节表示
        """
        if self._secret_key_cache is not None:
            return self._secret_key_cache

        with self._secret_key_lock:
            if self._secret_key_cache is not None:
                return self._secret_key_cache

            env_key = (settings.mcpcat_secret_key or '').strip()
            if env_key:
                self._secret_key_cache = env_key.encode('utf-8')
                return self._secret_key_cache

            config = self._config_service.load_config()
            app_config = config.get('app') or {}
            existing = app_config.get('secret_key')
            if existing:
                self._secret_key_cache = str(existing).strip().encode('utf-8')
                return self._secret_key_cache

            generated = secrets.token_urlsafe(48)
            config.setdefault('app', {})
            config['app']['secret_key'] = generated
            self._config_service.save_config(config)
            logger.warning(
                "未配置环境变量 MCPCAT_SECRET_KEY，已自动生成加密密钥并持久化到 config.json；"
                "该方式仅能避免敏感信息明文出现，无法抵御“拿到配置文件即可解密”的风险，"
                "生产环境请显式设置 MCPCAT_SECRET_KEY"
            )
            self._secret_key_cache = generated.encode('utf-8')
            return self._secret_key_cache

    @staticmethod
    def _keystream_byte(key: bytes, nonce: bytes, index: int) -> int:
        """基于 HMAC-SHA256 派生的简单密钥流（纯标准库 XOR 加密的基础）"""
        block = index // 32
        offset = index % 32
        material = nonce + block.to_bytes(8, 'big')
        digest = hashlib.sha256(key + material).digest()
        return digest[offset]

    def _encrypt_secret(self, plain: Optional[str]) -> Optional[str]:
        """
        加密敏感字符串（XOR + HMAC-SHA256，纯标准库实现，仅用于避免明文落盘）

        Args:
            plain: 明文，None 或空字符串时返回 None

        Returns:
            Optional[str]: Base64 编码的密文（包含 nonce 与签名），None 表示无需加密
        """
        if not plain:
            return None
        key = self._resolve_global_secret_key()
        nonce = os.urandom(16)
        raw = plain.encode('utf-8')
        encrypted = bytes(b ^ self._keystream_byte(key, nonce, i) for i, b in enumerate(raw))
        tag = hmac.new(key, nonce + encrypted, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(nonce + tag + encrypted).decode('utf-8')

    def _decrypt_secret(self, encoded: Optional[str]) -> Optional[str]:
        """
        解密 _encrypt_secret 产生的密文

        Args:
            encoded: Base64 编码的密文

        Returns:
            Optional[str]: 解密后的明文；密文格式无效或签名校验失败时返回 None 并记录日志
        """
        if not encoded:
            return None
        try:
            raw = base64.urlsafe_b64decode(encoded.encode('utf-8'))
        except Exception as e:
            logger.error(f"解密飞书 app_secret 失败，密文格式无效: {e}")
            return None

        if len(raw) < 48:
            logger.error("解密飞书 app_secret 失败，密文长度无效")
            return None

        nonce, tag, encrypted = raw[:16], raw[16:48], raw[48:]
        key = self._resolve_global_secret_key()
        expected_tag = hmac.new(key, nonce + encrypted, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected_tag):
            logger.error("解密飞书 app_secret 失败，签名校验未通过（加密密钥可能已变更）")
            return None

        plain = bytes(b ^ self._keystream_byte(key, nonce, i) for i, b in enumerate(encrypted))
        return plain.decode('utf-8')

    # ------------------------------------------------------------------
    # 飞书登录配置读写
    # ------------------------------------------------------------------

    def get_feishu_config(self) -> FeishuConfig:
        """
        获取飞书登录配置（app_secret 已解密为明文，仅供后端内部使用）

        Returns:
            FeishuConfig: 飞书登录配置
        """
        config = self._config_service.load_config()
        feishu_data = dict(config.get('feishu') or {})
        encrypted_secret = feishu_data.get('app_secret')
        feishu_data['app_secret'] = self._decrypt_secret(encrypted_secret)
        return FeishuConfig(**feishu_data)

    def update_feishu_settings(
        self,
        *,
        enabled: Optional[bool] = None,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        base_url: Optional[str] = None,
        default_permission: Optional[PermissionType] = None,
    ) -> FeishuConfig:
        """
        更新飞书登录配置（仅更新传入的字段，未提供的字段保持不变）

        Args:
            enabled: 是否启用飞书登录
            app_id: 飞书应用 App ID
            app_secret: 飞书应用 App Secret 明文（落盘前会被加密）
            base_url: 飞书开放平台 API 基础地址
            default_permission: 新用户默认权限

        Returns:
            FeishuConfig: 更新后的飞书登录配置
        """
        if app_secret is not None and app_secret.strip():
            # 提前解析（并按需持久化）全局加密密钥，避免下面整体保存配置时
            # 覆盖掉 _resolve_global_secret_key 内部刚写入的 app.secret_key
            self._resolve_global_secret_key()

        config = self._config_service.load_config()
        feishu_data = dict(config.get('feishu') or {})

        if enabled is not None:
            feishu_data['enabled'] = enabled
        if app_id is not None:
            feishu_data['app_id'] = app_id.strip() or None
        if base_url is not None:
            normalized = base_url.strip().rstrip('/')
            feishu_data['base_url'] = normalized or FeishuConfig().base_url
        if default_permission is not None:
            feishu_data['default_permission'] = PermissionType(default_permission).value
        if app_secret is not None:
            normalized_secret = app_secret.strip()
            feishu_data['app_secret'] = self._encrypt_secret(normalized_secret) if normalized_secret else None

        config['feishu'] = feishu_data
        self._config_service.save_config(config)
        logger.info("飞书登录配置已更新")
        return self.get_feishu_config()

    # ------------------------------------------------------------------
    # 飞书账号查找/创建与团队成员管理
    # ------------------------------------------------------------------

    def find_or_create_feishu_key(
        self,
        *,
        union_id: str,
        open_id: Optional[str],
        name: str,
        avatar_url: Optional[str],
    ) -> Tuple[APIKeyConfig, bool]:
        """
        按 feishu_union_id 查找受管账号，命中则刷新展示信息，未命中则自动创建

        Args:
            union_id: 飞书用户唯一标识
            open_id: 飞书应用内用户标识
            name: 飞书昵称
            avatar_url: 飞书头像地址

        Returns:
            Tuple[APIKeyConfig, bool]: (账号配置, 是否为首次登录新建)
        """
        # 提前解析全局加密密钥（用于读取 feishu.default_permission 所在配置段时
        # 解密 app_secret），避免其内部的持久化操作被本方法随后的整体保存覆盖
        self._resolve_global_secret_key()

        with self._feishu_login_lock:
            config = self._config_service.load_config()
            security_config = config.setdefault('security', {})
            security_config.setdefault('api_keys', [])
            security_config.setdefault('auth_header_name', 'Mcpcat-Key')
            api_keys = security_config['api_keys']

            for key_data in api_keys:
                if key_data.get('feishu_union_id') == union_id:
                    key_data['name'] = name
                    key_data['avatar_url'] = avatar_url
                    key_data['feishu_open_id'] = open_id
                    self._config_service.save_config(config)
                    key_config = APIKeyConfig(**self._process_datetime_fields(key_data))
                    logger.info(f"飞书用户登录(已存在账号): {name} ({union_id})")
                    return key_config, False

            feishu_config = self.get_feishu_config()
            new_key = APIKeyConfig(
                key=self.generate_api_key(),
                name=name,
                permission=feishu_config.default_permission,
                enabled=True,
                created_at=datetime.now(),
                feishu_union_id=union_id,
                feishu_open_id=open_id,
                avatar_url=avatar_url,
                source="feishu",
            )
            key_dict = new_key.dict()
            if key_dict.get('created_at'):
                key_dict['created_at'] = key_dict['created_at'].isoformat()
            if key_dict.get('expires_at'):
                key_dict['expires_at'] = key_dict['expires_at'].isoformat()

            api_keys.append(key_dict)
            self._config_service.save_config(config)
            logger.info(f"飞书用户首次登录，自动开通账号: {name} ({union_id})，权限={new_key.permission.value}")
            return new_key, True

    def list_feishu_accounts(self) -> List[APIKeyConfig]:
        """
        获取所有通过飞书登录创建的受管账号

        Returns:
            List[APIKeyConfig]: source 为 "feishu" 的账号列表
        """
        return [key for key in self.get_all_api_keys() if key.source == "feishu"]

    def update_feishu_account(
        self,
        union_id: str,
        *,
        permission: Optional[PermissionType] = None,
        enabled: Optional[bool] = None,
    ) -> Optional[APIKeyConfig]:
        """
        修改指定飞书绑定账号的权限和/或启用状态

        Args:
            union_id: 飞书用户唯一标识
            permission: 新的权限级别（None 表示不修改）
            enabled: 新的启用状态（None 表示不修改）

        Returns:
            Optional[APIKeyConfig]: 更新后的账号配置；账号不存在时返回 None
        """
        try:
            config = self._config_service.load_config()
            security_config = config.get('security', {})
            api_keys = security_config.get('api_keys', [])

            for key_data in api_keys:
                if key_data.get('source') == 'feishu' and key_data.get('feishu_union_id') == union_id:
                    if permission is not None:
                        key_data['permission'] = PermissionType(permission).value
                    if enabled is not None:
                        key_data['enabled'] = enabled
                    config['security']['api_keys'] = api_keys
                    self._config_service.save_config(config)
                    logger.info(f"更新飞书绑定账号: {union_id}")
                    return APIKeyConfig(**self._process_datetime_fields(key_data))

            return None

        except Exception as e:
            logger.error(f"更新飞书绑定账号时出错: {e}")
            return None


# 全局安全服务实例
security_service = SecurityService()