"""授权码/刷新共用 token 请求；保留旧 secret-post 默认，不跟随重定向。"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote_plus

import httpx
from mcp.client.auth.utils import issuers_match
from mcp.shared.auth import OAuthToken as ProtocolToken

from app.models.mcp_config import OAuthConfig, OAuthToken
from app.services.oauth_client_registration import credentials_compatible

logger = logging.getLogger(__name__)


def current_access_token(config: dict[str, Any]) -> str | None:
    """每次新连接读取当前 token，失效/删除/明确 issuer 不匹配时不发送旧凭证。"""
    oauth = config.get("oauth") or {}
    token = oauth.get("token") or {}
    access = token.get("access_token")
    if not isinstance(access, str) or not access:
        return None
    if not _issuer_matches(oauth):
        return None
    if token.get("expires_at"):
        try:
            expires = datetime.fromisoformat(token["expires_at"])
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) >= expires:
                return None
        except (ValueError, TypeError):
            return None
    return access


def _issuer_matches(oauth: dict[str, Any], include_token: bool = True) -> bool:
    issuer = oauth.get("issuer")
    if oauth.get("credentials_issuer"):
        try:
            if not credentials_compatible(OAuthConfig(**oauth)):
                return False
        except ValueError:
            return False
    if include_token:
        recorded = oauth.get("token_issuer")
        if recorded and (
            not isinstance(issuer, str)
            or not isinstance(recorded, str)
            or not issuers_match(recorded, issuer)
        ):
            return False
        recorded_client = oauth.get("token_client_id")
        if recorded_client and recorded_client != oauth.get("client_id"):
            return False
    return True


def _parameters(
    config: OAuthConfig, values: dict[str, str]
) -> tuple[dict[str, str], httpx.Auth | None]:
    data = {**values, "client_id": config.client_id or ""}
    method = getattr(config, "token_endpoint_auth_method", None)
    auth = None
    if method == "client_secret_basic":
        if not config.client_secret:
            raise ValueError("OAuth client secret missing")
        auth = httpx.BasicAuth(
            quote_plus(config.client_id or ""), quote_plus(config.client_secret)
        )
    elif method in (None, "client_secret_post"):
        if config.client_secret:
            data["client_secret"] = config.client_secret
    elif method != "none":
        raise ValueError("Unsupported OAuth token authentication")
    if getattr(config, "resource", None):
        data["resource"] = config.resource
    return data, auth


def _token(token: ProtocolToken, previous_refresh: str | None) -> OAuthToken | None:
    if not token.access_token:
        return None
    expires_at = None
    if token.expires_in is not None:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=token.expires_in)
        ).isoformat()
    return OAuthToken(
        access_token=token.access_token,
        refresh_token=token.refresh_token or previous_refresh,
        expires_at=expires_at,
        token_type=token.token_type,
    )


async def request_oauth_token(
    config: OAuthConfig,
    values: dict[str, str],
    previous_refresh: str | None = None,
) -> OAuthToken | None:
    """校验标准 token 响应，失败只记状态/异常类型，不记响应、授权码或凭证。"""
    if not config.token_endpoint:
        return None
    if not _issuer_matches(
        config.model_dump(), include_token=values.get("grant_type") == "refresh_token"
    ):
        return None
    try:
        data, auth = _parameters(config, values)
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.post(
                config.token_endpoint,
                data=data,
                auth=auth,
                headers={"Accept": "application/json"},
            )
        if response.status_code != 200:
            logger.warning("OAuth token 请求失败，HTTP %s", response.status_code)
            return None
        return _token(ProtocolToken.model_validate(response.json()), previous_refresh)
    except Exception as error:
        logger.warning("OAuth token 请求异常：%s", type(error).__name__)
        return None
