"""单次 OAuth 授权状态与回调校验；PKCE/issuer 使用 SDK 公开实现。"""

import secrets
import time
from collections.abc import Mapping
from copy import deepcopy
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

from mcp.client.auth import OAuthFlowError, PKCEParameters
from mcp.client.auth.utils import validate_authorization_response_iss
from mcp.shared.auth import AuthorizationCodeResult, OAuthMetadata
from pydantic import ValidationError

from app.models.mcp_config import OAuthConfig

_auth_states: dict[str, dict[str, Any]] = {}
_STATE_TTL = 300
_CONTEXT_FIELDS = (
    "authorization_endpoint",
    "token_endpoint",
    "client_id",
    "client_secret",
    "issuer",
    "resource",
    "scopes",
    "redirect_mode",
    "authorization_response_iss_parameter_supported",
    "credentials_issuer",
    "client_metadata_url",
    "token_endpoint_auth_method",
    "grant_type",
    "private_key_env",
    "signing_algorithm",
)


def authorization_context(config: OAuthConfig) -> dict[str, Any]:
    """仅用于内存相等性核验，包含秘密的结果不得记录日志或返回客户端。"""
    data = config.model_dump()
    return {name: data.get(name) for name in _CONTEXT_FIELDS}


def source_context(identity: object | None) -> dict[str, Any] | None:
    """绑定来源地址、传输、配置凭证及运行代际；仅用于内存比较。"""
    if not isinstance(identity, dict):
        return None
    config = identity.get("config") or {}
    return {
        "type": config.get("type"),
        "url": config.get("url"),
        "headers": deepcopy(config.get("headers")),
        "runtime": identity.get("mcp"),
    }


def _authorize_url(config: OAuthConfig, params: dict[str, str]) -> str:
    parsed = urlparse(config.authorization_endpoint or "")
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.netloc
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError("OAuth 授权端点配置无效")
    existing = [
        (name, value)
        for name, value in parse_qsl(parsed.query, max_num_fields=100)
        if name not in params and name not in {"scope", "resource"}
    ]
    return urlunparse(parsed._replace(query=urlencode(existing + list(params.items()))))


def _authorization_parameters(
    config: OAuthConfig, redirect: str, state: str, pkce: PKCEParameters
) -> dict[str, str]:
    params = {
        "response_type": "code",
        "client_id": config.client_id or "",
        "redirect_uri": redirect,
        "state": state,
        "code_challenge": pkce.code_challenge,
        "code_challenge_method": "S256",
    }
    if config.scopes:
        params["scope"] = " ".join(config.scopes)
    if getattr(config, "resource", None):
        params["resource"] = config.resource
    return params


def authorization_redirect(
    server_name: str, config: OAuthConfig, public_base_url: str | None
) -> tuple[str, str]:
    """DCR/CIMD 与授权请求使用同一个已选定的回调地址。"""
    mode = "auto" if config.redirect_mode == "auto" and public_base_url else "manual"
    redirect = (
        f"{public_base_url.rstrip('/')}/api/oauth/callback/{server_name}"
        if mode == "auto"
        else "http://localhost:8765/callback"
    )
    return mode, redirect


def begin_authorization(
    server_name: str,
    config: OAuthConfig,
    public_base_url: str | None = None,
    source_identity: object | None = None,
) -> dict[str, Any]:
    """冻结配置与来源，保持旧返回字段及五分钟、同服务单次授权语义。"""
    now = time.monotonic()
    for name, attempt in list(_auth_states.items()):
        if now - attempt["created_at"] > _STATE_TTL:
            _auth_states.pop(name, None)
    snapshot = config.model_copy(deep=True)
    methods = getattr(snapshot, "code_challenge_methods_supported", None)
    if methods is not None and "S256" not in methods:
        raise ValueError("OAuth 来源不支持 S256 PKCE")
    pkce = PKCEParameters.generate()
    state = secrets.token_urlsafe(32)
    mode, redirect = authorization_redirect(server_name, snapshot, public_base_url)
    params = _authorization_parameters(snapshot, redirect, state, pkce)
    url = _authorize_url(snapshot, params)
    _auth_states[server_name] = {
        "state": state,
        "created_at": now,
        "oauth_config": snapshot,
        "redirect_uri": redirect,
        "redirect_mode": mode,
        "code_verifier": pkce.code_verifier,
        "source_identity": source_identity,
        "source_context": source_context(source_identity),
    }
    return {
        "authorize_url": url,
        "redirect_mode": mode,
        "redirect_uri": redirect,
        "state": state,
    }


def parse_callback(query: Mapping[str, list[str]]) -> AuthorizationCodeResult | None:
    """拒绝重复/空/错误参数，不记录含授权码的 URL。"""
    if "error" in query or any(
        len(query.get(name, [])) != 1 for name in ("code", "state")
    ):
        return None
    if "iss" in query and len(query["iss"]) != 1:
        return None
    code, state = query["code"][0], query["state"][0]
    issuer = query.get("iss", [None])[0]
    if not code or not state or (issuer is not None and not issuer):
        return None
    return AuthorizationCodeResult(code=code, state=state, iss=issuer)


def parse_callback_url(url: str) -> AuthorizationCodeResult | None:
    """有界解析手动回调，具体 redirect_uri 在消耗状态时核验。"""
    try:
        return parse_callback(
            parse_qs(urlparse(url).query, keep_blank_values=True, max_num_fields=100)
        )
    except ValueError:
        return None


def _check_issuer(config: OAuthConfig, issuer: str | None) -> None:
    expected = getattr(config, "issuer", None)
    metadata = OAuthMetadata.model_validate(config.model_dump()) if expected else None
    if not expected and getattr(
        config, "authorization_response_iss_parameter_supported", False
    ):
        raise OAuthFlowError("Missing issuer binding")
    validate_authorization_response_iss(issuer, metadata)


def consume_authorization(
    server_name: str,
    state: str,
    *,
    issuer: str | None = None,
    source_identity: object | None = None,
    current_config: OAuthConfig | None = None,
    callback_url: str | None = None,
) -> dict[str, Any] | None:
    """校验一次性状态、来源、配置、redirect 与 issuer；任何拒绝均不发 token 请求。"""
    entry = _auth_states.get(server_name)
    if not entry or not secrets.compare_digest(entry["state"].encode(), state.encode()):
        return None
    _auth_states.pop(server_name, None)
    if time.monotonic() - entry["created_at"] > _STATE_TTL:
        return None
    config = entry["oauth_config"]
    if entry["source_identity"] is not source_identity:
        return None
    if entry["source_context"] != source_context(source_identity):
        return None
    if current_config and authorization_context(config) != authorization_context(
        current_config
    ):
        return None
    try:
        if callback_url:
            supplied, expected = urlparse(callback_url), urlparse(entry["redirect_uri"])
            if supplied.fragment or supplied.username or supplied.password:
                return None
            if (supplied.scheme, supplied.hostname, supplied.port, supplied.path) != (
                expected.scheme,
                expected.hostname,
                expected.port,
                expected.path,
            ):
                return None
        _check_issuer(config, issuer)
    except (ValueError, ValidationError, OAuthFlowError):
        return None
    return entry
