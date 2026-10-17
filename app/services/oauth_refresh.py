"""已有 OAuth token 的兼容刷新与迟到结果保护。"""

from copy import deepcopy
from typing import Any

from app.models.mcp_config import OAuthConfig
from app.services.config_service import ConfigService
from app.services.oauth_authorization import source_context
from app.services.oauth_flow import oauth_flow_service
from app.services.oauth_tokens import current_access_token


async def refresh_configured_source(manager: Any, name: str, value: dict) -> bool:
    """返回是否已处理已有 token；不让旧异步刷新覆盖新来源或更新后的配置。"""
    original = manager.server_info.get(name)
    oauth = value.get("oauth") or {}
    if not oauth.get("token"):
        return False
    if not original or original.get("config") is not value:
        return True
    if original.get("status") in {"stopping", "stopped"}:
        return True
    if current_access_token(value) is not None:
        original["auth_status"] = "authorized"
        return True
    snapshot, context = deepcopy(oauth), source_context(original)
    try:
        candidate = OAuthConfig(**snapshot)
    except ValueError:
        original["auth_status"] = "auth_expired"
        return True
    token = await oauth_flow_service.refresh_oauth_token(name, candidate)
    if (
        manager.server_info.get(name) is not original
        or source_context(original) != context
        or value.get("oauth") != snapshot
        or original.get("status") in {"stopping", "stopped"}
    ):
        return True
    if not token:
        original["auth_status"] = "auth_expired"
        return True
    candidate.token = token
    if getattr(candidate, "issuer", None):
        candidate.token_issuer = candidate.issuer
        if not getattr(candidate, "credentials_issuer", None):
            candidate.credentials_issuer = candidate.issuer
    if candidate.client_id:
        candidate.token_client_id = candidate.client_id
    if ConfigService.update_server_oauth(name, candidate):
        value["oauth"] = candidate.model_dump(exclude_none=True)
        original["auth_status"] = "authorized"
    else:
        original["auth_status"] = "auth_expired"
    return True
