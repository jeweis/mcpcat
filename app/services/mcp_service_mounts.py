"""复用本管理器已有代理路由；删除/重建不累积重复 Mount。"""

from typing import Any

from fastapi import FastAPI

from app.services.mcp_proxy_app import MCPProxyApp


def mount_service_routes(manager: Any, app: FastAPI, name: str) -> bool:
    """先核对两个地址的归属，不能覆盖别人的路由或吞掉挂载失败。"""
    pending = []
    for transport, path in (("mcp", f"/mcp/{name}"), ("sse", f"/sse/{name}")):
        existing = next(
            (
                route
                for route in app.router.routes
                if getattr(route, "path", None) == path
            ),
            None,
        )
        if existing is not None:
            proxy = getattr(existing, "app", None)
            if (
                not isinstance(proxy, MCPProxyApp)
                or proxy.server_manager is not manager
                or proxy.server_name != name
            ):
                return False
        else:
            pending.append((path, MCPProxyApp(name, manager, transport)))
    for path, proxy in pending:
        app.mount(path, proxy)
        if not any(mount["path"] == path for mount in manager.app_mount_list):
            manager.app_mount_list.append({"path": path, "app": proxy})
    manager._update_server_status(name, "mounted")
    return True
