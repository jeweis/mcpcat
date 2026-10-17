"""管理接入提示复用实际 Registry 校验，不创建身份或暴露认证秘密。"""

from typing import Any
from urllib.parse import urlsplit

from fastapi import Request

from app.models.mcp_config import PermissionType
from app.services.app_content_service import AppDomainError
from app.services.app_resource_registry import AppResourceRegistry
from app.services.app_tool_bindings import AppToolResolver, publication_validator
from app.services.app_tools_provider import app_open_name
from app.services.apps_mcp_settings import apps_mcp_config
from app.services.config_service import ConfigService
from app.services.security_service import security_service
from app.storage.unit_of_work import UnitOfWork


def _base_url(request: Request) -> str | None:
    """地址可以含部署前缀，但不把 userinfo/query 中的秘密送入 UI。"""
    value = ConfigService.get_public_base_url() or str(request.base_url)
    parts = urlsplit(value)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or any(character.isspace() for character in value)
    ):
        return None
    return value.rstrip("/")


def _authorized(request: Request) -> bool:
    """耗时内容校验之后仍检查当前 READ 身份。"""
    key = security_service.verify_api_key(
        request.headers.get(security_service.get_auth_header_name(), "")
    )
    return bool(key and security_service.has_permission(key, PermissionType.READ))


def _registry(request: Request, runtime: Any) -> AppResourceRegistry:
    """管理 READ 身份重新校验，不借用 MCP 请求的上下文或 WRITE 快照。"""

    def authorize() -> bool:
        return _authorized(request)

    resolver = AppToolResolver(
        request.app.state.database, request.app.state.server_manager, authorize
    )

    async def eligible(slug: str, version: str, info: dict) -> bool:
        await publication_validator(request.app.state.database, resolver, slug, version)
        return authorize()

    return AppResourceRegistry(
        request.app.state.database,
        instance=runtime.registry.instance,
        endpoint=runtime.registry.endpoint,
        authorize=authorize,
        available=runtime.available,
        eligible=eligible,
    )


async def app_connections(request: Request, detail: dict) -> dict:
    """只为实际可读取/可渲染的明确版本返回打开动作。"""
    runtime = getattr(request.app.state, "apps_mcp_runtime", None)
    config = apps_mcp_config()
    base = _base_url(request)
    registry = _registry(request, runtime) if runtime and runtime.available() else None
    with UnitOfWork(request.app.state.database) as unit:
        app_id = unit.apps.get(detail["slug"]).id
    result = []
    sources = ConfigService.load_mcp_servers_config()
    for version in detail["versions"]:
        item = {
            "version": version["version"],
            "ready": False,
            "endpoint_url": None,
            "resource_uri": None,
            "open_tool": None,
            "require_auth": config.require_auth
            or detail["visibility"] != "public"
            or any(
                sources.get(binding["server_name"], {}).get("require_auth", True)
                for binding in version["bindings"]
            ),
            "auth_header_name": security_service.get_auth_header_name(),
            "reason": (
                "app_not_published"
                if version["status"] != "published"
                else "apps_serving_not_ready"
            ),
        }
        if registry and base and version["status"] == "published":
            await _ready_connection(registry, app_id, item, base, config.path_name)
        result.append(item)
    if not _authorized(request):
        raise AppDomainError("当前身份无权读取 App 接入信息", 403)
    return {"slug": detail["slug"], "connections": result}


async def _ready_connection(
    registry: AppResourceRegistry, app_id: int, item: dict, base: str, path: str
) -> None:
    """读取制品、派生摘要和实时绑定通过后再显示地址，不执行上传代码。"""
    try:
        for identity in registry.published():
            if identity.version != item["version"] or identity.app_id != app_id:
                continue
            await registry.entry(identity.encode())
            item.update(
                ready=True,
                endpoint_url=f"{base}/mcp/{path}",
                resource_uri=identity.encode(),
                open_tool=app_open_name(identity),
                reason=None,
            )
            return
    except (AppDomainError, ValueError, OSError):
        item["reason"] = "app_content_or_binding_unavailable"
