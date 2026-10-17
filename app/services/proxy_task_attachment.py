"""HTTP 来源任务适配装配，复用来源订阅的实时权限和实例检查。"""

from typing import Any

from fastmcp.server.dependencies import get_http_request
from fastmcp.server.providers.proxy import FastMCPProxy

from app.services.proxy_clients import make_proxy_client_factory
from app.services.proxy_tasks import ProxyTasks
from app.services.security_service import security_service


def attach_proxy_tasks(
    server: Any, config: dict, subscriptions: Any
) -> ProxyTasks | None:
    """HTTP 跨请求连接，STDIO 持有原进程，旧 SSE 不声明现代 Tasks。"""
    if (
        config.get("type") not in {"streamable-http", "stdio"}
        or not isinstance(server, FastMCPProxy)
        or subscriptions is None
    ):
        return None

    def authorized() -> bool:
        try:
            return subscriptions.authorized(get_http_request())
        except RuntimeError:
            return False

    def principal() -> str:
        return get_http_request().headers.get(
            security_service.get_auth_header_name(), ""
        )

    adapter = ProxyTasks(
        make_proxy_client_factory(config),
        principal,
        authorized,
        retain_connection=config.get("type") == "stdio",
    )
    server.add_extension(adapter)
    return adapter
