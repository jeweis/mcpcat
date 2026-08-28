"""OAuth API 端点 - 处理 OAuth 授权流程"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.models.mcp_config import OAuthConfig
from app.services.config_service import ConfigService
from app.services.oauth_flow import oauth_flow_service
from app.services.server_manager import server_manager

logger = logging.getLogger(__name__)

router = APIRouter()


class ManualCallbackRequest(BaseModel):
    """手动粘贴回调请求"""

    callback_url: str


class SetCredentialsRequest(BaseModel):
    """手动设置 client_id / client_secret 请求"""

    client_id: str
    client_secret: Optional[str] = None


@router.post("/oauth/{server_name}/authorize")
async def authorize_server(server_name: str, request: Request):
    """
    触发 OAuth 授权流程，返回授权 URL + redirect_mode。
    如果 DCR 失败且无 client_id，返回 needs_credentials=true 让前端引导用户手动填写。
    """
    server_info = server_manager.server_info.get(server_name)
    if not server_info:
        raise HTTPException(status_code=404, detail=f"服务器 {server_name} 不存在")

    config = server_info.get("config")
    if not config:
        raise HTTPException(status_code=400, detail="缺少服务器配置")

    oauth_dict = config.get("oauth") if isinstance(config, dict) else None
    oauth_config = OAuthConfig(**oauth_dict) if oauth_dict else None

    if not oauth_config:
        server_url = config.get("url") if isinstance(config, dict) else None
        if not server_url:
            raise HTTPException(status_code=400, detail="该服务器类型不支持 OAuth")

        endpoints = await oauth_flow_service.discover_endpoints(server_url)
        if not endpoints or not endpoints.get("authorization_endpoint"):
            raise HTTPException(
                status_code=400,
                detail="无法发现 OAuth 端点，请手动配置 authorization_endpoint 和 token_endpoint",
            )

        oauth_config = OAuthConfig(
            authorization_endpoint=endpoints["authorization_endpoint"],
            token_endpoint=endpoints.get("token_endpoint"),
            registration_endpoint=endpoints.get("registration_endpoint"),
            scopes=endpoints.get("scopes_supported", []),
        )

    public_base_url = ConfigService.get_public_base_url() or str(
        request.base_url
    ).rstrip("/")

    # 如果没有 client_id，尝试 DCR
    if not oauth_config.client_id:
        if oauth_config.registration_endpoint:
            redirect_mode = oauth_config.redirect_mode
            if redirect_mode == "auto" and public_base_url:
                redirect_uri = (
                    f"{public_base_url.rstrip('/')}/api/oauth/callback/{server_name}"
                )
            else:
                redirect_uri = "http://localhost:8765/callback"

            result = await oauth_flow_service.dynamic_client_register(
                oauth_config.registration_endpoint, [redirect_uri]
            )
            if result:
                oauth_config.client_id, oauth_config.client_secret = result
                ConfigService.update_server_oauth(server_name, oauth_config)
                # 更新内存中的 config
                if isinstance(config, dict):
                    config["oauth"] = oauth_config.dict(exclude_none=True)
            else:
                # DCR 失败，返回 needs_credentials 让前端引导用户手动填写
                ConfigService.update_server_oauth(server_name, oauth_config)
                if isinstance(config, dict):
                    config["oauth"] = oauth_config.dict(exclude_none=True)
                return {
                    "needs_credentials": True,
                    "registration_endpoint": oauth_config.registration_endpoint,
                    "message": "动态客户端注册失败，请在该服务的开发者后台注册 OAuth 应用，然后填写 client_id 和 client_secret",
                }
        else:
            # 无 DCR 端点，需要手动填写
            ConfigService.update_server_oauth(server_name, oauth_config)
            if isinstance(config, dict):
                config["oauth"] = oauth_config.dict(exclude_none=True)
            return {
                "needs_credentials": True,
                "registration_endpoint": None,
                "message": "该服务不支持动态客户端注册，请手动填写 client_id 和 client_secret",
            }

    # 已有 client_id，生成授权 URL
    result = oauth_flow_service.generate_authorize_url(
        server_name, oauth_config, public_base_url
    )
    result["needs_credentials"] = False
    return result


@router.post("/oauth/{server_name}/set-credentials")
async def set_client_credentials(server_name: str, body: SetCredentialsRequest):
    """手动设置 OAuth client_id / client_secret，然后生成授权 URL"""
    server_info = server_manager.server_info.get(server_name)
    if not server_info:
        raise HTTPException(status_code=404, detail=f"服务器 {server_name} 不存在")

    config = server_info.get("config")
    if not config or not isinstance(config, dict):
        raise HTTPException(status_code=400, detail="缺少服务器配置")

    oauth_dict = config.get("oauth") or {}
    oauth_config = OAuthConfig(**oauth_dict) if oauth_dict else OAuthConfig()

    oauth_config.client_id = body.client_id
    if body.client_secret is not None:
        oauth_config.client_secret = body.client_secret

    ConfigService.update_server_oauth(server_name, oauth_config)
    config["oauth"] = oauth_config.dict(exclude_none=True)

    # 生成授权 URL
    public_base_url = ConfigService.get_public_base_url()
    result = oauth_flow_service.generate_authorize_url(
        server_name, oauth_config, public_base_url
    )
    result["needs_credentials"] = False
    return result


@router.get("/oauth/callback/{server_name}")
async def oauth_callback_auto(server_name: str, request: Request):
    """自动回调端点 - 第三方服务直接重定向到此处"""
    code = request.query_params.get("code")
    state = request.query_params.get("state")

    if not code or not state:
        return HTMLResponse(
            "<html><body><h2>回调参数缺失</h2><p>缺少 code 或 state 参数。</p></body></html>",
            status_code=400,
        )

    token = await oauth_flow_service.exchange_code_for_token(server_name, code, state)
    if not token:
        return HTMLResponse(
            "<html><body><h2>授权失败</h2><p>Token 交换失败，请重试。</p></body></html>",
            status_code=400,
        )

    _persist_token(server_name, token)

    server_manager._update_server_status(server_name, "running")

    return HTMLResponse(
        "<html><body><h2>授权成功</h2><p>窗口可以关闭，请返回 mcpcat 管理界面查看。</p>"
        "<script>setTimeout(()=>window.close(),3000);</script></body></html>"
    )


@router.post("/oauth/{server_name}/callback")
async def oauth_callback_manual(server_name: str, body: ManualCallbackRequest):
    """手动粘贴回调端点 - 用户粘贴回调 URL"""
    parsed = oauth_flow_service.handle_manual_callback(server_name, body.callback_url)
    if not parsed:
        raise HTTPException(
            status_code=400, detail="回调 URL 格式错误，缺少 code 或 state"
        )

    code, state = parsed
    token = await oauth_flow_service.exchange_code_for_token(server_name, code, state)
    if not token:
        raise HTTPException(status_code=400, detail="Token 交换失败")

    _persist_token(server_name, token)

    server_manager._update_server_status(server_name, "running")

    return {"status": "ok", "message": "授权成功"}


@router.get("/oauth/{server_name}/status")
async def get_oauth_status(server_name: str):
    """查询 OAuth 授权状态"""
    server_info = server_manager.server_info.get(server_name)
    if not server_info:
        raise HTTPException(status_code=404, detail=f"服务器 {server_name} 不存在")

    auth_status = server_info.get("auth_status", "none")
    return {"server_name": server_name, "auth_status": auth_status}


def _persist_token(server_name: str, token):
    """将 token 持久化到 server_info 和 SQLite"""
    server_info = server_manager.server_info.get(server_name)
    if not server_info or not server_info.get("config"):
        return
    cfg = server_info["config"]
    if not isinstance(cfg, dict):
        return
    oauth_dict = cfg.get("oauth") or {}
    oauth_dict["token"] = token.dict(exclude_none=True)
    cfg["oauth"] = oauth_dict
    ConfigService.update_server_oauth(server_name, OAuthConfig(**oauth_dict))
    server_info["auth_status"] = "authorized"
