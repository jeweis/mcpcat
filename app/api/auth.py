"""认证相关API"""

import json
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from app.models.mcp_config import PermissionType
from app.services.feishu_auth_service import FeishuAPIError, feishu_auth_service
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


class FeishuStatusResponse(BaseModel):
    """飞书登录状态响应模型"""
    enabled: bool
    app_id: Optional[str] = None


class FeishuAuthorizeUrlResponse(BaseModel):
    """飞书授权链接响应模型"""
    authorize_url: str


class FeishuLoginRequest(BaseModel):
    """飞书授权码登录请求模型"""
    code: str
    redirect_uri: Optional[str] = None


class FeishuLoginUser(BaseModel):
    """飞书登录响应中的用户信息"""
    name: str
    permission: str


class FeishuLoginResponse(BaseModel):
    """飞书授权码登录响应模型"""
    token: str
    user: FeishuLoginUser
    first_login: bool


class FeishuSettingsResponse(BaseModel):
    """飞书应用配置响应模型（不包含 app_secret 明文）"""
    enabled: bool
    app_id: Optional[str] = None
    base_url: str
    default_permission: str
    has_app_secret: bool


class UpdateFeishuSettingsRequest(BaseModel):
    """更新飞书应用配置请求模型（仅传入字段会被更新）"""
    enabled: Optional[bool] = None
    app_id: Optional[str] = None
    app_secret: Optional[str] = None
    base_url: Optional[str] = None
    default_permission: Optional[str] = None


class FeishuAccountItem(BaseModel):
    """飞书绑定账号列表项"""
    union_id: str
    name: str
    avatar_url: Optional[str] = None
    permission: str
    enabled: bool
    created_at: Optional[datetime] = None


class UpdateFeishuAccountRequest(BaseModel):
    """更新飞书绑定账号请求模型（仅传入字段会被更新）"""
    permission: Optional[str] = None
    enabled: Optional[bool] = None


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


def _feishu_account_to_item(key_config) -> FeishuAccountItem:
    """将受管账号转换为飞书绑定账号列表项"""
    return FeishuAccountItem(
        union_id=key_config.feishu_union_id,
        name=key_config.name,
        avatar_url=key_config.avatar_url,
        permission=key_config.permission.value,
        enabled=key_config.enabled,
        created_at=key_config.created_at,
    )


@router.get("/auth/feishu/status")
async def get_feishu_status():
    """
    获取飞书登录状态（公开接口）

    供前端判断是否在登录页展示"飞书登录"入口
    """
    feishu_config = security_service.get_feishu_config()
    enabled = bool(feishu_config.enabled and feishu_config.app_id and feishu_config.app_secret)
    return FeishuStatusResponse(
        enabled=enabled,
        app_id=feishu_config.app_id if enabled else None
    )


@router.get("/auth/feishu/authorize-url")
async def get_feishu_authorize_url(redirect_uri: str, state: Optional[str] = None):
    """
    生成飞书授权链接（公开接口）

    Args:
        redirect_uri: 飞书授权完成后的回调地址（必填）
        state: 透传的状态参数（可选，用于防 CSRF 与回跳）
    """
    feishu_config = security_service.get_feishu_config()
    if not (feishu_config.enabled and feishu_config.app_id and feishu_config.app_secret):
        raise HTTPException(
            status_code=400,
            detail={"code": "FEISHU_NOT_ENABLED", "message": "飞书登录未启用或配置不完整"}
        )

    normalized_redirect_uri = (redirect_uri or "").strip()
    if not normalized_redirect_uri:
        raise HTTPException(
            status_code=400,
            detail={"code": "FEISHU_REDIRECT_URI_REQUIRED", "message": "redirect_uri 不能为空"}
        )

    query = {
        "app_id": feishu_config.app_id,
        "redirect_uri": normalized_redirect_uri,
        "response_type": "code",
        "scope": "contact:user.base:readonly",
    }
    normalized_state = (state or "").strip()
    if normalized_state:
        query["state"] = normalized_state

    authorize_url = (
        f"{feishu_config.base_url.rstrip('/')}/open-apis/authen/v1/authorize?{urlencode(query)}"
    )
    return FeishuAuthorizeUrlResponse(authorize_url=authorize_url)


@router.post("/auth/feishu/login")
async def feishu_login(payload: FeishuLoginRequest):
    """
    使用飞书授权码完成登录（公开接口）

    串联"换取 user_access_token → 获取用户信息 → 查找或创建受管账号"，
    返回的 token 即可直接作为 Mcpcat-Key 使用
    """
    feishu_config = security_service.get_feishu_config()
    if not (feishu_config.enabled and feishu_config.app_id and feishu_config.app_secret):
        raise HTTPException(
            status_code=400,
            detail={"code": "FEISHU_NOT_ENABLED", "message": "飞书登录未启用或配置不完整"}
        )

    code = (payload.code or "").strip()
    if not code:
        raise HTTPException(
            status_code=400,
            detail={"code": "FEISHU_AUTH_CODE_INVALID", "message": "授权码不能为空"}
        )

    try:
        user_access_token = feishu_auth_service.exchange_code_v2(
            base_url=feishu_config.base_url,
            app_id=feishu_config.app_id,
            app_secret=feishu_config.app_secret,
            code=code,
            redirect_uri=payload.redirect_uri,
        )
        user_info = feishu_auth_service.get_user_info(
            base_url=feishu_config.base_url,
            user_access_token=user_access_token,
        )
    except FeishuAPIError as e:
        logger.warning(f"飞书登录失败: {e.code} - {e.message} | details: {json.dumps(e.details, ensure_ascii=False)}")
        raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message})

    key_config, first_login = security_service.find_or_create_feishu_key(
        union_id=user_info.union_id,
        open_id=user_info.open_id,
        name=user_info.name,
        avatar_url=user_info.avatar_url,
    )

    logger.info(f"飞书登录成功: {key_config.name} (first_login={first_login})")
    return FeishuLoginResponse(
        token=key_config.key,
        user=FeishuLoginUser(name=key_config.name, permission=key_config.permission.value),
        first_login=first_login,
    )


@router.get("/admin/feishu/settings")
async def get_feishu_settings():
    """
    查看飞书应用配置（需要写权限）

    响应中不包含 app_secret 明文，仅返回 has_app_secret 标记
    """
    feishu_config = security_service.get_feishu_config()
    return FeishuSettingsResponse(
        enabled=feishu_config.enabled,
        app_id=feishu_config.app_id,
        base_url=feishu_config.base_url,
        default_permission=feishu_config.default_permission.value,
        has_app_secret=bool(feishu_config.app_secret),
    )


@router.put("/admin/feishu/settings")
async def update_feishu_settings(payload: UpdateFeishuSettingsRequest):
    """
    更新飞书应用配置（需要写权限）

    仅更新请求中提供的字段；app_secret 在持久化前会被加密
    """
    default_permission: Optional[PermissionType] = None
    if payload.default_permission is not None:
        try:
            default_permission = PermissionType(payload.default_permission)
        except ValueError:
            raise HTTPException(status_code=400, detail="无效的默认权限级别，仅支持 read/write")

    updated = security_service.update_feishu_settings(
        enabled=payload.enabled,
        app_id=payload.app_id,
        app_secret=payload.app_secret,
        base_url=payload.base_url,
        default_permission=default_permission,
    )
    return FeishuSettingsResponse(
        enabled=updated.enabled,
        app_id=updated.app_id,
        base_url=updated.base_url,
        default_permission=updated.default_permission.value,
        has_app_secret=bool(updated.app_secret),
    )


@router.get("/admin/feishu/users", response_model=List[FeishuAccountItem])
async def list_feishu_users():
    """查看团队成员列表（需要写权限）：返回所有通过飞书登录创建的受管账号"""
    accounts = security_service.list_feishu_accounts()
    return [_feishu_account_to_item(account) for account in accounts]


@router.put("/admin/feishu/users/{union_id}")
async def update_feishu_user(union_id: str, payload: UpdateFeishuAccountRequest):
    """
    修改指定飞书绑定账号的权限和/或启用状态（需要写权限）

    变更对该账号下一次请求即时生效，无需用户重新登录
    """
    permission: Optional[PermissionType] = None
    if payload.permission is not None:
        try:
            permission = PermissionType(payload.permission)
        except ValueError:
            raise HTTPException(status_code=400, detail="无效的权限级别，仅支持 read/write")

    updated = security_service.update_feishu_account(union_id, permission=permission, enabled=payload.enabled)
    if not updated:
        raise HTTPException(status_code=404, detail="未找到该飞书绑定账号")

    logger.info(f"管理员更新飞书绑定账号: {union_id}")
    return _feishu_account_to_item(updated)