"""企业微信登录及企业自建应用的管理员配置/成员接口。"""

import logging
import secrets
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, ValidationError

from app.models.mcp_config import PermissionType
from app.models.wecom import WeComConfig
from app.services.config_service import ConfigService
from app.services.wecom_account_service import wecom_account_service
from app.services.wecom_auth_service import WeComAPIError, wecom_auth_service
from app.storage.unit_of_work import UnitOfWork
from app.storage.wecom_transactions import WeComTransactions, digest

logger = logging.getLogger(__name__)
router = APIRouter()
COOKIE_NAME = "mcpcat_wecom_browser"
PRIVATE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


class UpdateWeComSettings(BaseModel):
    """只更新提供的字段；空白 Secret 保持不变。"""

    enabled: Optional[bool] = None
    corp_id: Optional[str] = Field(default=None, max_length=255)
    agent_id: Optional[str] = Field(default=None, max_length=32)
    app_secret: Optional[str] = Field(default=None, max_length=1024)
    redirect_uri: Optional[str] = Field(default=None, max_length=2048)
    default_permission: Optional[PermissionType] = None


class UpdateWeComMember(BaseModel):
    """受管成员仅允许更新权限和启用状态。"""

    permission: Optional[PermissionType] = None
    enabled: Optional[bool] = None


class WeComLoginRequest(BaseModel):
    """前端提交短期一次性票据，不接收企微原始授权码。"""

    ticket: str = Field(min_length=1, max_length=128)


def _error(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"code": code, "message": message},
        headers=PRIVATE_HEADERS,
    )


def _configured() -> WeComConfig:
    config = wecom_account_service.config()
    if not (
        config.enabled
        and config.corp_id
        and config.agent_id
        and config.app_secret
        and config.redirect_uri
    ):
        raise _error("WECOM_NOT_ENABLED", "企业微信登录未启用或配置不完整")
    return config


def _fingerprint(config: WeComConfig) -> str:
    return digest(config.model_dump_json())


def _settings_response(config: WeComConfig) -> dict:
    return {
        **config.model_dump(mode="json", exclude={"app_secret"}),
        "has_app_secret": bool(config.app_secret),
    }


@router.get("/auth/wecom/status")
def status(response: Response) -> dict:
    """公开状态，仅在配置齐全且开启时展示登录按钮。"""
    response.headers.update(PRIVATE_HEADERS)
    config = wecom_account_service.config()
    return {
        "enabled": bool(
            config.enabled
            and config.corp_id
            and config.agent_id
            and config.app_secret
            and config.redirect_uri
        )
    }


@router.get("/auth/wecom/authorize-url")
def authorize_url(request: Request, response: Response) -> dict:
    """生成浏览器绑定的随机 state，使用管理员固定回调地址。"""
    config = _configured()
    browser = request.cookies.get(COOKIE_NAME) or secrets.token_urlsafe(32)
    state = WeComTransactions(ConfigService._require_database()).issue(
        "state",
        browser,
        {"config": _fingerprint(config)},
        600,
    )
    response.headers.update(PRIVATE_HEADERS)
    response.set_cookie(
        COOKIE_NAME,
        browser,
        max_age=600,
        httponly=True,
        secure=config.redirect_uri.startswith("https://"),
        samesite="lax",
        path="/",
    )
    return {"authorize_url": wecom_auth_service.authorize_url(config, state)}


@router.get("/auth/wecom/callback")
def callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    """校验 state 后交换身份，前端只收到短期、浏览器绑定的票据。"""
    config = _configured()
    transactions = WeComTransactions(ConfigService._require_database())
    browser = request.cookies.get(COOKIE_NAME, "")
    payload = transactions.consume("state", state, browser)
    error_code = None
    if not payload or payload.get("config") != _fingerprint(config):
        error_code = "WECOM_STATE_INVALID"
    elif not code.strip() or len(code) > 2048:
        error_code = "WECOM_CODE_REQUIRED"
    else:
        try:
            user = wecom_auth_service.exchange_code(config, code)
            # 网络调用期间配置发生改变时，不创建账号/签发凭证。
            if _fingerprint(_configured()) != _fingerprint(config):
                raise _error("WECOM_CONFIG_CHANGED", "登录配置已变更，请重新登录")
            key, created = wecom_account_service.find_or_create(config, user)
            if not wecom_account_service.security.verify_api_key(key.key):
                error_code = "WECOM_ACCOUNT_UNAVAILABLE"
            else:
                ticket = transactions.issue(
                    "ticket",
                    browser,
                    {
                        "corp_id": config.corp_id,
                        "user_id": user.user_id,
                        "key_fingerprint": digest(key.key),
                        "config": _fingerprint(config),
                        "first_login": created,
                    },
                    60,
                )
                return RedirectResponse(
                    config.frontend_callback + "?" + urlencode({"ticket": ticket}),
                    status_code=303,
                    headers=PRIVATE_HEADERS,
                )
        except WeComAPIError as error:
            logger.warning(
                "企业微信登录失败: %s upstream_code=%s", error.code, error.upstream_code
            )
            error_code = error.code
        except HTTPException:
            error_code = "WECOM_CONFIG_CHANGED"
    return RedirectResponse(
        config.frontend_callback + "?" + urlencode({"error": error_code}),
        status_code=303,
        headers=PRIVATE_HEADERS,
    )


@router.post("/auth/wecom/login")
def login(payload: WeComLoginRequest, request: Request, response: Response) -> dict:
    """一次性兑换本地 Key，兑换时再次检查配置和成员权限状态。"""
    config = _configured()
    data = WeComTransactions(ConfigService._require_database()).consume(
        "ticket",
        payload.ticket,
        request.cookies.get(COOKIE_NAME, ""),
    )
    if not data or data.get("config") != _fingerprint(config):
        raise _error("WECOM_TICKET_INVALID", "登录凭证已失效，请重新登录")
    with UnitOfWork(ConfigService._require_database()) as unit:
        key_data = unit.api_keys.get_by_wecom_identity(data["corp_id"], data["user_id"])
    key = (
        wecom_account_service.security.verify_api_key(key_data["key"])
        if key_data
        and key_data.get("source") == "wecom"
        and digest(key_data["key"]) == data.get("key_fingerprint")
        else None
    )
    if key is None:
        raise _error(
            "WECOM_ACCOUNT_UNAVAILABLE", "账号已禁用、过期或删除，请联系管理员", 403
        )
    response.headers.update(PRIVATE_HEADERS)
    return {
        "token": key.key,
        "user": {"name": key.name, "permission": key.permission.value},
        "first_login": data["first_login"],
    }


@router.get("/admin/wecom/settings")
def get_settings(response: Response) -> dict:
    """管理员查看配置，响应中永不返回 Secret。"""
    response.headers.update(PRIVATE_HEADERS)
    return _settings_response(wecom_account_service.config())


@router.put("/admin/wecom/settings")
def update_settings(payload: UpdateWeComSettings, response: Response) -> dict:
    """保存完整校验后的自建应用配置。"""
    try:
        config = wecom_account_service.update_settings(
            payload.model_dump(exclude_unset=True)
        )
    except (ValidationError, ValueError):
        raise _error(
            "WECOM_SETTINGS_INVALID",
            "配置不完整或格式不正确：请检查 CorpID、正整数 AgentID、Secret 和 HTTPS 回调地址",
        ) from None
    except RuntimeError:
        raise _error(
            "WECOM_SETTINGS_SAVE_FAILED", "企业微信设置保存失败", 500
        ) from None
    response.headers.update(PRIVATE_HEADERS)
    return _settings_response(config)


@router.get("/admin/wecom/users")
def list_members() -> list[dict]:
    """返回企微受管成员列表，不返回 API Key。"""
    return wecom_account_service.list_members()


@router.put("/admin/wecom/users/{record_id}")
def update_member(record_id: int, payload: UpdateWeComMember) -> dict:
    """复用 read/write 权限，禁用后现有 Key 随即失效。"""
    updated = wecom_account_service.update_member(
        record_id,
        {
            key: value
            for key, value in payload.model_dump(
                mode="json", exclude_unset=True
            ).items()
            if value is not None
        },
    )
    if updated is None:
        raise _error("WECOM_MEMBER_NOT_FOUND", "企业微信成员不存在", 404)
    return updated
