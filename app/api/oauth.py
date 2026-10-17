"""OAuth API 端点 - 处理 OAuth 授权流程"""

import logging
from copy import deepcopy
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from mcp.shared.auth import AuthorizationCodeResult
from pydantic import BaseModel, Field

from app.models.mcp_config import OAuthConfig, OAuthToken
from app.services.config_service import ConfigService
from app.services.oauth_authorization import (
    authorization_context,
    authorization_redirect,
    parse_callback,
    parse_callback_url,
    source_context,
)
from app.services.oauth_client_registration import (
    ClientIssuerMismatch,
    select_oauth_client,
)
from app.services.oauth_flow import oauth_flow_service
from app.services.oauth_probe import OAuthProbeError
from app.services.server_manager import server_manager

logger = logging.getLogger(__name__)

router = APIRouter()


class ManualCallbackRequest(BaseModel):
    """手动粘贴回调请求"""

    callback_url: str


class SetCredentialsRequest(BaseModel):
    """手动设置 client_id / client_secret 请求"""

    client_id: str = Field(min_length=1)
    client_secret: Optional[str] = None


@router.post("/oauth/{server_name}/authorize")
async def authorize_server(server_name: str, request: Request) -> dict:
    """选择已有/CIMD/DCR 身份，无法自动注册时保留原手工凭证引导。"""
    server_info = server_manager.server_info.get(server_name)
    if not server_info:
        raise HTTPException(status_code=404, detail=f"服务器 {server_name} 不存在")

    config = server_info.get("config")
    if not config:
        raise HTTPException(status_code=400, detail="缺少服务器配置")

    _require_interactive_oauth(config)

    original_context = source_context(server_info)
    original_oauth = deepcopy(config.get("oauth"))
    oauth_config = await _load_oauth_configuration(config)
    public_base_url = ConfigService.get_public_base_url() or str(
        request.base_url
    ).rstrip("/")

    _, redirect_uri = authorization_redirect(server_name, oauth_config, public_base_url)
    try:
        selected, needs_credentials = await select_oauth_client(
            oauth_config, redirect_uri
        )
    except ClientIssuerMismatch:
        return _needs_credentials(
            oauth_config, "授权服务器已变化，请重新配置该服务器的客户端凭证"
        )
    except ValueError:
        raise HTTPException(
            status_code=400, detail="OAuth 客户端配置无效，请检查 issuer、CIMD 与端点"
        ) from None
    if (
        server_manager.server_info.get(server_name) is not server_info
        or source_context(server_info) != original_context
        or config.get("oauth") != original_oauth
    ):
        raise HTTPException(
            status_code=409, detail="授权期间来源或配置已变化，请重新发起授权"
        )
    if (
        selected.model_dump() != oauth_config.model_dump()
        or needs_credentials
        or original_oauth is None
    ):
        _save_oauth_configuration(server_name, server_info, selected)
    if needs_credentials:
        return _needs_credentials(
            selected, "无法自动注册客户端，请手动填写 client_id 和 client_secret"
        )
    return _begin_authorization(server_name, server_info, selected, public_base_url)


async def _load_oauth_configuration(config: dict) -> OAuthConfig:
    if config.get("oauth"):
        return OAuthConfig(**config["oauth"])
    if not config.get("url"):
        raise HTTPException(status_code=400, detail="该服务器类型不支持 OAuth")
    try:
        endpoints = await oauth_flow_service.discover_endpoints(
            config["url"], config.get("headers"), config.get("type", "streamable-http")
        )
    except OAuthProbeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None
    if not endpoints or not endpoints.get("authorization_endpoint"):
        raise HTTPException(
            status_code=400, detail="无法发现 OAuth 端点，请手动配置授权与令牌端点"
        )
    return OAuthConfig(**endpoints, scopes=endpoints.get("scopes_supported", []))


def _require_interactive_oauth(config: dict) -> None:
    """旧浏览器授权操作不能修改明确配置的机器认证来源。"""
    if (config.get("oauth") or {}).get("grant_type") == "client_credentials":
        raise HTTPException(
            status_code=409,
            detail="该来源使用机器认证，请编辑服务端认证配置，无需浏览器登录",
        )


def _needs_credentials(config: OAuthConfig, message: str) -> dict:
    return {
        "needs_credentials": True,
        "registration_endpoint": config.registration_endpoint,
        "message": message,
    }


def _save_oauth_configuration(name: str, info: dict, config: OAuthConfig) -> None:
    if not ConfigService.update_server_oauth(name, config):
        raise HTTPException(status_code=500, detail="OAuth 配置保存失败，请重试")
    info["config"]["oauth"] = config.model_dump(exclude_none=True)


def _begin_authorization(
    name: str, info: dict, config: OAuthConfig, base_url: str | None
) -> dict:
    try:
        result = oauth_flow_service.generate_authorize_url(name, config, base_url, info)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="OAuth 授权配置无效，请检查端点和 PKCE"
        ) from None
    return {**result, "needs_credentials": False}


@router.post("/oauth/{server_name}/set-credentials")
async def set_client_credentials(server_name: str, body: SetCredentialsRequest) -> dict:
    """手动设置 OAuth client_id / client_secret，然后生成授权 URL"""
    server_info = server_manager.server_info.get(server_name)
    if not server_info:
        raise HTTPException(status_code=404, detail=f"服务器 {server_name} 不存在")

    config = server_info.get("config")
    if not config or not isinstance(config, dict):
        raise HTTPException(status_code=400, detail="缺少服务器配置")

    _require_interactive_oauth(config)

    oauth_dict = config.get("oauth") or {}
    oauth_config = OAuthConfig(**oauth_dict) if oauth_dict else OAuthConfig()
    changed = oauth_config.client_id != body.client_id or (
        body.client_secret is not None
        and oauth_config.client_secret != body.client_secret
    )
    if oauth_config.client_id != body.client_id and body.client_secret is None:
        oauth_config.client_secret = None
    oauth_config.client_id = body.client_id
    if body.client_secret is not None:
        oauth_config.client_secret = body.client_secret
    if changed:
        oauth_config.token = None
        oauth_config.token_issuer = None
        oauth_config.token_client_id = None
        oauth_config.token_endpoint_auth_method = (
            "client_secret_post" if oauth_config.client_secret else "none"
        )
    if getattr(oauth_config, "issuer", None):
        oauth_config.credentials_issuer = oauth_config.issuer
    oauth_config.client_registration_mode = "pre_registered"
    _save_oauth_configuration(server_name, server_info, oauth_config)
    if changed:
        server_info["auth_status"] = "auth_required"

    # 生成授权 URL
    public_base_url = ConfigService.get_public_base_url()
    try:
        result = oauth_flow_service.generate_authorize_url(
            server_name, oauth_config, public_base_url, server_info
        )
    except ValueError:
        raise HTTPException(
            status_code=400, detail="OAuth 授权配置无效，请检查端点和 PKCE"
        ) from None
    result["needs_credentials"] = False
    return result


@router.get("/oauth/callback/{server_name}")
async def oauth_callback_auto(server_name: str, request: Request) -> HTMLResponse:
    """自动回调端点 - 第三方服务直接重定向到此处"""
    parsed = parse_callback(
        {name: request.query_params.getlist(name) for name in request.query_params}
    )
    if parsed is None:
        return HTMLResponse(
            "<html><body><h2>回调参数缺失</h2><p>缺少 code 或 state 参数。</p></body></html>",
            status_code=400,
        )

    if not await _complete_callback(server_name, parsed):
        return HTMLResponse(
            "<html><body><h2>授权失败</h2><p>Token 交换失败，请重试。</p></body></html>",
            status_code=400,
        )

    return HTMLResponse(
        "<html><body><h2>授权成功</h2><p>窗口可以关闭，请返回 mcpcat 管理界面查看。</p>"
        "<script>setTimeout(()=>window.close(),3000);</script></body></html>"
    )


@router.post("/oauth/{server_name}/callback")
async def oauth_callback_manual(server_name: str, body: ManualCallbackRequest) -> dict:
    """手动粘贴回调端点 - 用户粘贴回调 URL"""
    parsed = parse_callback_url(body.callback_url)
    if not parsed:
        raise HTTPException(
            status_code=400, detail="回调 URL 格式错误，缺少 code 或 state"
        )

    if not await _complete_callback(server_name, parsed, body.callback_url):
        raise HTTPException(status_code=400, detail="Token 交换失败")

    return {"status": "ok", "message": "授权成功"}


@router.get("/oauth/{server_name}/status")
async def get_oauth_status(server_name: str):
    """查询 OAuth 授权状态"""
    server_info = server_manager.server_info.get(server_name)
    if not server_info:
        raise HTTPException(status_code=404, detail=f"服务器 {server_name} 不存在")

    auth_status = server_info.get("auth_status", "none")
    return {"server_name": server_name, "auth_status": auth_status}


async def _complete_callback(
    server_name: str, parsed: AuthorizationCodeResult, callback_url: str | None = None
) -> bool:
    """令牌请求前后都核对同一运行来源和授权配置，不把迟到 token 写给新来源。"""
    info = server_manager.server_info.get(server_name)
    if (
        not parsed.state
        or not info
        or not isinstance(info.get("config"), dict)
        or info.get("status") in {"stopping", "stopped"}
    ):
        return False
    config = OAuthConfig(**(info["config"].get("oauth") or {}))
    if getattr(config, "grant_type", None) == "client_credentials":
        return False
    context = source_context(info)
    token = await oauth_flow_service.exchange_code_for_token(
        server_name,
        parsed.code,
        parsed.state,
        issuer=parsed.iss,
        source_identity=info,
        current_config=config,
        callback_url=callback_url,
    )
    if (
        not token
        or server_manager.server_info.get(server_name) is not info
        or info.get("status") in {"stopping", "stopped"}
    ):
        return False
    current = OAuthConfig(**(info["config"].get("oauth") or {}))
    if context != source_context(info) or authorization_context(
        config
    ) != authorization_context(current):
        return False
    if not _persist_token(server_name, token):
        return False
    server_manager._update_server_status(server_name, "running")
    return True


def _persist_token(server_name: str, token: OAuthToken) -> bool:
    """将 token 持久化到 server_info 和 SQLite"""
    server_info = server_manager.server_info.get(server_name)
    if not server_info or not server_info.get("config"):
        return False
    cfg = server_info["config"]
    if not isinstance(cfg, dict):
        return False
    oauth_dict = dict(cfg.get("oauth") or {})
    oauth_dict["token"] = token.dict(exclude_none=True)
    if oauth_dict.get("issuer"):
        oauth_dict["token_issuer"] = oauth_dict["issuer"]
        oauth_dict.setdefault("credentials_issuer", oauth_dict["issuer"])
    if oauth_dict.get("client_id"):
        oauth_dict["token_client_id"] = oauth_dict["client_id"]
    if not ConfigService.update_server_oauth(server_name, OAuthConfig(**oauth_dict)):
        return False
    cfg["oauth"] = oauth_dict
    server_info["auth_status"] = "authorized"
    return True
