"""认证中间件"""

import re
from typing import List, Pattern
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.services.security_service import security_service
from app.services.config_service import ConfigService
from app.models.mcp_config import PermissionType
from app.exceptions.auth import AuthenticationError, PermissionDeniedError
import logging

logger = logging.getLogger(__name__)




class AuthMiddleware(BaseHTTPMiddleware):
    """API Key认证中间件"""
    
    def __init__(self, app, public_paths: List[str] = None):
        super().__init__(app)
        
        # 配置服务实例
        self.config_service = ConfigService()
        
        # 公开路径（无需认证）
        self.public_paths = public_paths or [
            r"^/$",                          # 根路径
            r"^/ui/.*",                      # 前端资源
            r"^/static/.*",                  # 静态文件
            r"^/api/health$",                # 健康检查
            r"^/api/auth/verify$",           # 登录验证
            r"^/api/auth/config$",           # 认证配置
            r"^/api/auth/first-run-keys$",   # 首次运行Key获取
        ]
        
        # 编译正则表达式
        self.public_patterns: List[Pattern] = [
            re.compile(pattern) for pattern in self.public_paths
        ]
        
        # 权限映射：路径模式 -> 所需权限
        # 注意：更具体的模式应该放在前面，因为会按顺序匹配
        self.permission_map = {
            # 管理API - write权限 (具体的操作端点)
            r"^/api/servers/[^/]+/start$": PermissionType.WRITE,
            r"^/api/servers/[^/]+/stop$": PermissionType.WRITE,
            r"^/api/servers/[^/]+/restart$": PermissionType.WRITE,
            
            # 管理API - read权限 (查看端点)
            r"^/api/servers/[^/]+/health$": PermissionType.READ,
            r"^/api/servers/[^/]+/config$": PermissionType.READ,
            
            # MCP协议端点 - read权限
            r"^/mcp/[^/]+/.*": PermissionType.READ,
            
            # SSE端点 - read权限  
            r"^/sse/[^/]+.*": PermissionType.READ,
        }
        
        # 编译权限映射的正则表达式
        self.permission_patterns = {
            re.compile(pattern): permission 
            for pattern, permission in self.permission_map.items()
        }
    
    def is_public_path(self, path: str) -> bool:
        """检查路径是否为公开路径"""
        return any(pattern.match(path) for pattern in self.public_patterns)
    
    def is_mcp_server_public(self, path: str) -> bool:
        """
        检查MCP服务器端点是否配置为公开访问
        
        Args:
            path: 请求路径
            
        Returns:
            bool: 如果服务器配置为不需要认证则返回True
        """
        # 匹配MCP端点: /mcp/server_name/...
        mcp_match = re.match(r"^/mcp/([^/]+)", path)
        if mcp_match:
            server_name = mcp_match.group(1)
            return self._check_server_auth_config(server_name)
        
        # 匹配SSE端点: /sse/server_name/...
        sse_match = re.match(r"^/sse/([^/]+)", path)
        if sse_match:
            server_name = sse_match.group(1)
            return self._check_server_auth_config(server_name)
        
        return False
    
    def _check_server_auth_config(self, server_name: str) -> bool:
        """
        检查指定服务器的认证配置
        
        Args:
            server_name: 服务器名称
            
        Returns:
            bool: 如果服务器配置为不需要认证则返回True
        """
        try:
            config = self.config_service.load_config()
            mcp_servers = config.get('mcpServers', {})
            server_config = mcp_servers.get(server_name, {})
            
            # 默认需要认证，除非明确配置为不需要
            require_auth = server_config.get('require_auth', True)
            return not require_auth
            
        except Exception as e:
            logger.error(f"检查服务器认证配置时出错: {e}")
            # 出错时默认需要认证，更安全
            return False
    
    def get_required_permission(self, path: str, method: str) -> PermissionType:
        """
        获取路径所需的权限
        
        Args:
            path: 请求路径
            method: HTTP方法
            
        Returns:
            PermissionType: 所需权限，默认为READ
        """
        # 检查特定的权限映射
        for pattern, permission in self.permission_patterns.items():
            if pattern.match(path):
                return permission
        
        # 对于API管理端点，根据HTTP方法和路径判断权限
        if path.startswith('/api/servers'):
            method_upper = method.upper()
            
            # GET /api/servers - 列出服务器 (read权限)
            if path == '/api/servers' and method_upper == 'GET':
                return PermissionType.READ
            
            # POST /api/servers - 添加服务器 (write权限)
            if path == '/api/servers' and method_upper == 'POST':
                return PermissionType.WRITE
            
            # GET /api/servers/{name} - 获取服务器详情 (read权限)
            if path.startswith('/api/servers/') and method_upper == 'GET':
                return PermissionType.READ
            
            # PUT /api/servers/{name} - 更新服务器 (write权限)
            # DELETE /api/servers/{name} - 删除服务器 (write权限)
            if path.startswith('/api/servers/') and method_upper in ['PUT', 'DELETE']:
                return PermissionType.WRITE
        
        # 默认情况下，POST/PUT/DELETE需要write权限，GET需要read权限
        if method.upper() in ['POST', 'PUT', 'DELETE']:
            return PermissionType.WRITE
        else:
            return PermissionType.READ
    
    async def dispatch(self, request: Request, call_next):
        """中间件主要逻辑"""
        path = request.url.path
        method = request.method
        
        # 检查是否为公开路径
        if self.is_public_path(path):
            return await call_next(request)
        
        # 检查是否为配置为公开的MCP服务器端点
        if self.is_mcp_server_public(path):
            logger.debug(f"MCP服务器端点无需认证: {method} {path}")
            return await call_next(request)
        
        try:
            # 获取动态认证头名称
            auth_header_name = security_service.get_auth_header_name()
            
            # 获取API Key
            api_key = request.headers.get(auth_header_name)
            if not api_key:
                logger.warning(f"未提供API Key: {method} {path}")
                return JSONResponse(
                    status_code=401,
                    content={"detail": "API Key required"},
                    headers={"WWW-Authenticate": auth_header_name}
                )
            
            # 验证API Key
            key_config = security_service.verify_api_key(api_key)
            if not key_config:
                logger.warning(f"无效的API Key: {method} {path}")
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid API Key"},
                    headers={"WWW-Authenticate": auth_header_name}
                )
            
            # 检查权限
            required_permission = self.get_required_permission(path, method)
            if not security_service.has_permission(key_config, required_permission):
                logger.warning(f"权限不足: {key_config.name} 尝试访问 {method} {path}")
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Permission denied"}
                )
            
            # 将用户信息添加到请求状态中
            request.state.user = {
                "name": key_config.name,
                "permission": key_config.permission.value,
                "api_key": api_key
            }
            
            # 记录成功的认证
            logger.debug(f"认证成功: {key_config.name} ({key_config.permission.value}) -> {method} {path}")
            
            return await call_next(request)
            
        except Exception as e:
            logger.error(f"认证中间件出错: {e}")
            return JSONResponse(
                status_code=500,
                content={"detail": "Authentication error"}
            )


def get_current_user(request: Request) -> dict:
    """
    从请求中获取当前用户信息
    
    Args:
        request: FastAPI请求对象
        
    Returns:
        dict: 用户信息
    """
    return getattr(request.state, 'user', None)


def require_permission(required_permission: PermissionType):
    """
    权限检查装饰器
    
    Args:
        required_permission: 所需权限
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # 从参数中找到request对象
            request = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            
            if not request:
                raise AuthenticationError("Request object not found")
            
            user = get_current_user(request)
            if not user:
                raise AuthenticationError("User not authenticated")
            
            # 检查权限
            user_permission = PermissionType(user['permission'])
            if user_permission == PermissionType.WRITE:
                # write权限可以访问所有内容
                pass
            elif user_permission != required_permission:
                raise PermissionDeniedError(f"Requires {required_permission.value} permission")
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator
