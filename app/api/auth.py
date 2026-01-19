"""认证相关API"""

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from app.services.security_service import security_service
from app.middleware.auth import get_current_user
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


class VerifyRequest(BaseModel):
    """验证请求模型（可选，也可以通过Header传递）"""
    api_key: str


class UserInfo(BaseModel):
    """用户信息响应模型"""
    name: str
    permission: str
    authenticated: bool = True


@router.post("/auth/verify")
async def verify_api_key(request: Request):
    """
    验证API Key并返回用户信息
    用于前端登录验证
    """
    try:
        # 获取动态认证头名称
        auth_header_name = security_service.get_auth_header_name()
        
        # 从Header获取API Key
        api_key = request.headers.get(auth_header_name)
        
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail=f"API Key required in {auth_header_name} header"
            )
        
        # 验证API Key
        key_config = security_service.verify_api_key(api_key)
        if not key_config:
            raise HTTPException(
                status_code=401,
                detail="Invalid API Key"
            )
        
        # 返回用户信息
        user_info = UserInfo(
            name=key_config.name,
            permission=key_config.permission.value
        )
        
        logger.info(f"API Key验证成功: {key_config.name}")
        return user_info
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"验证API Key时出错: {e}")
        raise HTTPException(
            status_code=500,
            detail="Verification failed"
        )


@router.get("/auth/info")
async def get_current_user_info(request: Request):
    """
    获取当前用户信息
    需要认证
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated"
        )
    
    return UserInfo(
        name=user['name'],
        permission=user['permission']
    )


@router.get("/auth/config")
async def get_auth_config():
    """
    获取认证配置信息（公开接口）
    用于前端获取认证头名称等配置
    """
    return {
        "auth_header_name": security_service.get_auth_header_name()
    }


@router.get("/auth/first-run-keys")
async def get_first_run_keys():
    """
    获取首次运行时自动生成的 API Key（公开接口，仅返回一次）

    用于前端首次登录时展示自动生成的 Key，调用后立即清除。
    如果用户通过环境变量设置了 Key，则不会返回。
    """
    keys = security_service.get_first_run_keys()

    if keys is None:
        return {"has_keys": False}

    # 获取后立即清除，确保只展示一次
    security_service.clear_first_run_keys()

    return {
        "has_keys": True,
        "admin_key": keys.get("admin_key"),
        "read_key": keys.get("read_key"),
        "admin_key_name": keys.get("admin_key_name"),
        "read_key_name": keys.get("read_key_name")
    }