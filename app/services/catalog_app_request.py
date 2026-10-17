"""Catalog App 请求身份；REST 显式传递，MCP 从当前 HTTP 上下文读取。"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from fastmcp.server.dependencies import get_http_request

from app.models.mcp_config import PermissionType
from app.services.config_service import ConfigService
from app.services.security_service import security_service

_REST_KEY: ContextVar[str | None] = ContextVar("catalog_app_rest_key", default=None)


@contextmanager
def catalog_request(key: str) -> Iterator[None]:
    """只由可信 REST 边界注入，不从客户端 metadata 或工具参数取身份。"""
    token = _REST_KEY.set(key)
    try:
        yield
    finally:
        _REST_KEY.reset(token)


def authorize_catalog_source(source: dict) -> bool:
    """公开 Catalog 不会使私有来源公开；错误 Key 不回退匿名访问。"""
    config = ConfigService.get_setting_section("catalog", {}) or {}
    if not config.get("enabled", True):
        return False
    key = _REST_KEY.get()
    if key is None:
        try:
            key = get_http_request().headers.get(
                security_service.get_auth_header_name(), ""
            )
        except RuntimeError:
            key = ""
    if key:
        row = security_service.verify_api_key(key)
        return bool(row and security_service.has_permission(row, PermissionType.READ))
    return not config.get("require_auth", True) and not source.get("require_auth", True)
