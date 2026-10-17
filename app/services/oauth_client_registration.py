"""OAuth 客户端选择；复用 SDK 的 DCR 模型/响应校验、CIMD 与 issuer 绑定。"""

import logging
from urllib.parse import urlparse

import httpx2
from mcp.client.auth import OAuthRegistrationError
from mcp.client.auth.oauth2 import check_registration_usable
from mcp.client.auth.utils import (
    create_client_info_from_metadata_url,
    create_client_registration_request,
    credentials_match_issuer,
    handle_registration_response,
    is_valid_client_metadata_url,
    should_use_client_metadata_url,
)
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
)
from pydantic import AnyUrl

from app.models.mcp_config import OAuthConfig
from app.services.async_compat import operation_timeout

logger = logging.getLogger(__name__)
_METHODS = ("client_secret_basic", "client_secret_post", "none")


class ClientIssuerMismatch(ValueError):
    """已绑定的 client 不属于当前 issuer，不能默默跨来源复用。"""


def _metadata(config: OAuthConfig) -> OAuthMetadata | None:
    return (
        OAuthMetadata.model_validate(config.model_dump())
        if getattr(config, "issuer", None)
        else None
    )


def credentials_compatible(config: OAuthConfig) -> bool:
    """旧未绑定记录按 SDK 兼容，CIMD 仅按明确 URL 身份识别为可移植。"""
    issuer = getattr(config, "issuer", None)
    recorded = getattr(config, "credentials_issuer", None)
    if recorded and not issuer:
        return False
    info = OAuthClientInformationFull(client_id=config.client_id or "", issuer=recorded)
    return not issuer or credentials_match_issuer(
        info, issuer, getattr(config, "client_metadata_url", None)
    )


def _valid_metadata_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        parsed.port
        return (
            is_valid_client_metadata_url(url)
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and not parsed.fragment
            and not any(ord(char) <= 32 or ord(char) == 127 for char in url)
        )
    except ValueError:
        return False


def _registration_request(
    config: OAuthConfig, redirect_uris: list[str]
) -> httpx2.Request:
    metadata = _metadata(config)
    supported = metadata.token_endpoint_auth_methods_supported if metadata else None
    method = next(
        (name for name in _METHODS if supported is None or name in supported), None
    )
    if method is None:
        raise ValueError("Unsupported OAuth token authentication")
    client = OAuthClientMetadata(
        redirect_uris=[AnyUrl(uri) for uri in redirect_uris],
        client_name="mcpcat",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=" ".join(config.scopes) or None,
        token_endpoint_auth_method=method,
        application_type=(
            "native"
            if urlparse(redirect_uris[0]).hostname in {"localhost", "127.0.0.1", "::1"}
            else "web"
        ),
    )
    if metadata:
        return create_client_registration_request(
            metadata, client, config.registration_endpoint
        )
    return httpx2.Request(
        "POST",
        config.registration_endpoint,
        json=client.model_dump(mode="json", exclude_none=True),
    )


async def register_oauth_client(
    config: OAuthConfig,
    redirect_uri: str,
    additional_redirect_uris: list[str] | None = None,
) -> OAuthClientInformationFull | None:
    """单次 DCR，不发送原 client_secret/token，不跟随重定向或自动重试。"""
    if not config.registration_endpoint:
        return None
    try:
        request = _registration_request(
            config, [redirect_uri, *(additional_redirect_uris or [])]
        )
        async with operation_timeout(15), httpx2.AsyncClient(
            timeout=10, follow_redirects=False
        ) as client:
            response = await client.send(request, stream=True)
            try:
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > 256 * 1024:
                        raise ValueError("OAuth registration response too large")
                    body.extend(chunk)
                parsed = httpx2.Response(
                    response.status_code, content=bytes(body), request=request
                )
            finally:
                await response.aclose()
        info = await handle_registration_response(parsed)
        info.token_endpoint_auth_method = (
            info.token_endpoint_auth_method or "client_secret_basic"
        )
        check_registration_usable(info)
        if info.token_endpoint_auth_method not in _METHODS or not info.client_id:
            raise OAuthRegistrationError("Unsupported registered client")
        if info.redirect_uris and AnyUrl(redirect_uri) not in info.redirect_uris:
            raise OAuthRegistrationError("Registration changed redirect URI")
        info.issuer = getattr(config, "issuer", None)
        return info
    except Exception as error:
        logger.warning("OAuth 客户端注册失败：%s", type(error).__name__)
        return None


def _apply_client(
    config: OAuthConfig, info: OAuthClientInformationFull, mode: str, redirect_uri: str
) -> OAuthConfig:
    config.client_id, config.client_secret = info.client_id, info.client_secret
    config.token_endpoint_auth_method = info.token_endpoint_auth_method
    config.client_registration_mode = mode
    config.registered_redirect_uris = [
        str(uri) for uri in info.redirect_uris or [redirect_uri]
    ]
    if getattr(config, "issuer", None):
        config.credentials_issuer = config.issuer
    return config


async def select_oauth_client(
    config: OAuthConfig, redirect_uri: str
) -> tuple[OAuthConfig, bool]:
    """已有身份 → 明确支持的 CIMD → DCR；bool 表示是否仍需手工凭证。"""
    candidate = config.model_copy(deep=True)
    methods = getattr(candidate, "code_challenge_methods_supported", None)
    if methods is not None and "S256" not in methods:
        raise ValueError("OAuth 来源不支持 S256 PKCE")
    if candidate.client_id:
        if not credentials_compatible(candidate):
            raise ClientIssuerMismatch(
                "OAuth 客户端不属于当前授权服务器，请重新配置凭证"
            )
        registered = getattr(candidate, "registered_redirect_uris", None)
        if (
            getattr(candidate, "client_registration_mode", None) == "dcr"
            and registered
            and redirect_uri not in registered
        ):
            return candidate, True
        return candidate, False
    metadata_url = getattr(candidate, "client_metadata_url", None)
    if metadata_url and not _valid_metadata_url(metadata_url):
        raise ValueError("OAuth CIMD URL 必须是无凭证的 HTTPS 文档地址")
    if should_use_client_metadata_url(_metadata(candidate), metadata_url):
        info = create_client_info_from_metadata_url(
            metadata_url, [AnyUrl(redirect_uri)]
        )
        return _apply_client(candidate, info, "cimd", redirect_uri), False
    info = await register_oauth_client(candidate, redirect_uri)
    return (
        (_apply_client(candidate, info, "dcr", redirect_uri), False)
        if info
        else (candidate, True)
    )
