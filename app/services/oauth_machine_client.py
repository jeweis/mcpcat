"""上游机器认证装配；配置来源独立，不复用交互式 OAuth 的已存令牌。"""

from typing import Any
from urllib.parse import urlsplit

from mcp.client.auth.extensions.client_credentials import (
    ClientCredentialsOAuthProvider,
    PrivateKeyJWTOAuthProvider,
)
from mcp.client.auth.utils import issuers_match
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from app.services.oauth_machine_guard import MachineOAuthGuard, machine_http_client
from app.services.oauth_machine_jwt import machine_assertion_provider


class MachineTokenStorage:
    """仅在当前客户端生命周期内缓存；不跨来源或重建恢复相对有效期。"""

    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        """返回当前客户端的令牌。"""
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """保存 SDK 已验证且通过范围检查的令牌。"""
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """实现 SDK 公开存储契约。"""
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """仅保存当前客户端信息，不执行动态注册。"""
        self.client_info = client_info


def _url(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("机器认证必须配置来源、issuer 和令牌地址")
    try:
        parts = urlsplit(value)
        valid = parts.scheme == "https" or (
            parts.scheme == "http"
            and parts.hostname in {"localhost", "127.0.0.1", "::1"}
        )
        if (
            not valid
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.fragment
        ):
            raise ValueError
        _ = parts.port
    except ValueError:
        raise ValueError(
            "机器认证地址必须使用 HTTPS（本机测试除外），且不含凭证或片段"
        ) from None
    return value


def _scopes(scopes: Any) -> list[str]:
    if not isinstance(scopes, list) or not all(
        isinstance(scope, str)
        and scope
        and all(
            ord(char) == 33 or 35 <= ord(char) <= 91 or 93 <= ord(char) <= 126
            for char in scope
        )
        for scope in scopes
    ):
        raise ValueError("机器认证必须显式配置合法的 scopes 列表")
    return scopes


def machine_client_options(config: dict[str, Any]) -> dict[str, Any] | None:
    """显式机器授权才装配；旧配置返回 None，非法配置不降级为匿名。"""
    oauth = config.get("oauth") or {}
    if oauth.get("grant_type") != "client_credentials":
        return None
    if config.get("type") not in {"streamable-http", "sse"}:
        raise ValueError("机器认证仅用于远程 MCP 来源")
    method = oauth.get("token_endpoint_auth_method", "client_secret_basic")
    if method not in {"client_secret_basic", "client_secret_post", "private_key_jwt"}:
        raise ValueError("不支持的机器认证凭证方式")
    client_id, secret = oauth.get("client_id"), oauth.get("client_secret")
    required = (client_id,) if method == "private_key_jwt" else (client_id, secret)
    if not all(isinstance(value, str) and value.strip() for value in required):
        raise ValueError("机器认证缺少客户端 ID 或密钥")
    scopes = _scopes(oauth.get("scopes"))
    if any(name.lower() == "authorization" for name in (config.get("headers") or {})):
        raise ValueError("机器认证不能与静态 Authorization 头同时配置")
    endpoint = _url(oauth.get("token_endpoint"))
    issuer = _url(oauth.get("issuer"))
    if oauth.get("credentials_issuer") and not issuers_match(
        str(oauth["credentials_issuer"]), issuer
    ):
        raise ValueError("机器认证凭证不属于当前 issuer")
    options = dict(
        server_url=_url(config.get("url")),
        issuer=issuer,
        client_id=client_id,
        scope=" ".join(scopes),
        storage=MachineTokenStorage(),
    )
    provider = (
        PrivateKeyJWTOAuthProvider(
            **options, assertion_provider=machine_assertion_provider(oauth, client_id)
        )
        if method == "private_key_jwt"
        else ClientCredentialsOAuthProvider(
            **options, client_secret=secret, token_endpoint_auth_method=method
        )
    )
    return {
        "auth": MachineOAuthGuard(provider, token_endpoint=endpoint, scopes=scopes),
        "httpx_client_factory": machine_http_client,
    }
