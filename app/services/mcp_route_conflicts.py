"""已挂载 MCP 路径归属检查，不通过路由顺序静默覆盖已有端点。"""

from fastapi import FastAPI


def mcp_route_occupied(app: FastAPI, name: str, *, owner: object | None = None) -> bool:
    """关闭但尚未卸载的系统端点仍保留其实际路由归属。"""
    paths = {f"/mcp/{name}", f"/sse/{name}"}
    for route in app.router.routes:
        if getattr(route, "path", "").rstrip("/") not in paths:
            continue
        target = getattr(route, "app", None)
        if (
            owner is not None
            and getattr(target, "server_manager", None) is owner
            and getattr(target, "server_name", None) == name
        ):
            continue
        return True
    return False
