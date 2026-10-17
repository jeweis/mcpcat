"""测试台只读授权诊断，复用现有发现，不执行登录或修改凭证。"""

import asyncio
from copy import deepcopy
from typing import Any

import anyio

from app.services.oauth_metadata import discover_oauth_endpoints
from app.services.oauth_probe import OAuthProbeError, probe_mcp_auth
from app.services.oauth_tokens import current_access_token
from app.services.proxy_clients import make_proxy_client_factory

_slots = asyncio.Semaphore(4)
_categories = {
    "timeout",
    "connection",
    "protocol_version",
    "http",
    "protocol",
    "redirect",
    "oauth_metadata_url",
    "oauth_metadata_size",
    "oauth_metadata_http",
    "oauth_resource_mismatch",
    "oauth_issuer_mismatch",
}


def auth_snapshot(info: dict[str, Any] | None) -> dict:
    """不回传 URL、凭证、challenge 或自定义配置值。"""
    if info is None:
        return {"status": "not_configured", "probe_available": False}
    config = info.get("config") or {}
    kind = config.get("type")
    oauth = config.get("oauth") or {}
    saved = info.get("auth_status", "none")
    machine = oauth.get("grant_type") == "client_credentials"
    return {
        "status": "not_checked",
        "probe_available": kind in {"sse", "streamable-http"},
        "transport": (
            kind
            if kind in {"sse", "streamable-http", "stdio", "openapi"}
            else "unknown"
        ),
        "saved_status": (
            saved
            if saved
            in {
                "none",
                "authorized",
                "auth_required",
                "auth_expired",
                "auth_unsupported",
            }
            else "unknown"
        ),
        "oauth_configured": bool(oauth),
        "machine_auth": machine,
        "credential_usable": not machine and current_access_token(config) is not None,
        "static_headers_configured": bool(config.get("headers")),
    }


async def diagnose_upstream(info: dict[str, Any]) -> dict:
    """只探测已配置来源，使用该来源凭证，不接受浏览器传入 URL。"""
    snapshot = auth_snapshot(info)
    if not snapshot["probe_available"]:
        return {**snapshot, "status": "not_applicable"}
    config = deepcopy(info.get("config") or {})
    if snapshot.get("machine_auth"):
        return await _diagnose_machine(config, snapshot)
    headers = dict(config.get("headers") or {})
    token = current_access_token(config)
    if token:
        headers = {
            key: value
            for key, value in headers.items()
            if key.lower() != "authorization"
        }
        headers["Authorization"] = f"Bearer {token}"
    try:
        with anyio.fail_after(45):
            async with _slots:
                result = await probe_mcp_auth(config["url"], headers, config["type"])
                if result.challenge is None:
                    return {
                        **snapshot,
                        "status": "reachable",
                        "protocol": result.protocol_version,
                    }
                metadata = await discover_oauth_endpoints(
                    config["url"], result.challenge
                )
                return {
                    **snapshot,
                    "status": (
                        "authorization_required"
                        if metadata
                        else "authorization_unavailable"
                    ),
                }
    except TimeoutError:
        return {**snapshot, "status": "error", "category": "timeout"}
    except OAuthProbeError as error:
        category = error.category if error.category in _categories else "oauth_metadata"
        return {**snapshot, "status": "error", "category": category}
    except Exception:
        return {**snapshot, "status": "error", "category": "configuration_or_protocol"}


async def _diagnose_machine(config: dict[str, Any], snapshot: dict) -> dict:
    """显式探测可申请短期机器令牌，但不持久化、不调用工具或改写配置。"""
    try:
        with anyio.fail_after(45):
            async with _slots:
                async with make_proxy_client_factory(config)() as client:
                    return {
                        **snapshot,
                        "status": "reachable",
                        "protocol": client.protocol_version,
                    }
    except TimeoutError:
        return {**snapshot, "status": "error", "category": "timeout"}
    except Exception:
        return {**snapshot, "status": "error", "category": "machine_auth_or_connection"}
