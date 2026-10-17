"""复用 SDK OAuth 发现顺序/模型/issuer 校验，不执行交互或发送凭证。"""

from typing import Any
from urllib.parse import urlparse

import httpx2
from mcp.client.auth import OAuthFlowError
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_oauth_metadata_request,
    extract_resource_metadata_from_www_auth,
    handle_auth_metadata_response,
    handle_protected_resource_response,
    validate_metadata_issuer,
)
from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata
from mcp.shared.auth_utils import check_resource_allowed, resource_url_from_server_url

from app.services.async_compat import operation_timeout
from app.services.oauth_probe import OAuthProbeError


def _safe_url(url: str) -> None:
    try:
        parsed = urlparse(url)
        parsed.port
    except ValueError:
        raise OAuthProbeError("oauth_metadata_url") from None
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        any(ord(character) <= 32 or ord(character) == 127 for character in url)
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.scheme not in {"http", "https"}
        or (parsed.scheme != "https" and not loopback)
    ):
        raise OAuthProbeError("oauth_metadata_url")


async def _fetch(client: httpx2.AsyncClient, url: str) -> httpx2.Response:
    _safe_url(url)
    request = create_oauth_metadata_request(url)
    async with client.stream("GET", url, headers=dict(request.headers)) as response:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > 256 * 1024:
                raise OAuthProbeError("oauth_metadata_size")
            body.extend(chunk)
        return httpx2.Response(
            response.status_code,
            headers=response.headers,
            content=bytes(body),
            request=request,
        )


async def _resource(
    client: httpx2.AsyncClient, url: str, challenge: httpx2.Response | None
) -> ProtectedResourceMetadata | None:
    advertised = (
        extract_resource_metadata_from_www_auth(challenge)
        if challenge is not None
        else None
    )
    candidates = build_protected_resource_metadata_discovery_urls(advertised, url)
    for candidate in dict.fromkeys(candidates):
        response = await _fetch(client, candidate)
        if response.status_code >= 500:
            raise OAuthProbeError("oauth_metadata_http")
        metadata = await handle_protected_resource_response(response)
        if metadata:
            if not check_resource_allowed(
                resource_url_from_server_url(url), str(metadata.resource)
            ):
                raise OAuthProbeError("oauth_resource_mismatch")
            return metadata
    return None


async def discover_oauth_endpoints(
    url: str,
    challenge: httpx2.Response | None = None,
) -> dict[str, Any] | None:
    """PRM → 授权服务器/OIDC；仅无 PRM 时兼容旧根目录发现。"""
    try:
        async with operation_timeout(30), httpx2.AsyncClient(
            timeout=10, follow_redirects=False
        ) as client:
            resource = await _resource(client, url, challenge)
            issuers = list(resource.authorization_servers or []) if resource else []
            if resource and not issuers:
                return None
            issuer = str(issuers[0]) if issuers else None
            if issuer:
                _safe_url(issuer)
            candidates = build_oauth_authorization_server_metadata_discovery_urls(
                issuer, url
            )
            for candidate in dict.fromkeys(candidates):
                response = await _fetch(client, candidate)
                proceed, metadata = await handle_auth_metadata_response(response)
                if not proceed:
                    raise OAuthProbeError("oauth_metadata_http")
                if metadata:
                    expected = (
                        issuer or f"{urlparse(url).scheme}://{urlparse(url).netloc}"
                    )
                    validate_metadata_issuer(metadata, expected)
                    return _endpoints(metadata, resource)
            return None
    except OAuthProbeError:
        raise
    except (
        TimeoutError,
        httpx2.HTTPError,
        httpx2.InvalidURL,
        OAuthFlowError,
        ValueError,
    ):
        raise OAuthProbeError("oauth_metadata") from None


def _endpoints(
    metadata: OAuthMetadata, resource: ProtectedResourceMetadata | None
) -> dict[str, Any]:
    data = metadata.model_dump(mode="json", exclude_none=True)
    for name in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        if data.get(name):
            _safe_url(data[name])
    if resource:
        data["resource"] = str(resource.resource)
        data["scopes_supported"] = resource.scopes_supported or data.get(
            "scopes_supported", []
        )
    return data
