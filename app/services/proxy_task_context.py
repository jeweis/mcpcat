"""代理任务的客户端直接调用边界，不读取框架私有分发标记。"""

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator
from typing import Any

from fastmcp.exceptions import NotFoundError
from fastmcp.server.dependencies import extract_version_spec, get_http_request
from fastmcp.server.providers.proxy import ProxyTool
from fastmcp.utilities.versions import VersionSpec
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

_dispatching = ContextVar("mcpcat_proxy_task_dispatching", default=False)


@contextmanager
def task_dispatch() -> Iterator[bool]:
    """嵌套工具及其子任务继承禁止开启新代理任务的标记。"""
    outermost = not _dispatching.get()
    token = _dispatching.set(True)
    try:
        yield outermost
    finally:
        _dispatching.reset(token)


async def requested_tool(name: str) -> bool:
    """HTTP 原始调用必须确实命中此工具；资源/提示内部调用不算直接请求。"""
    try:
        request = get_http_request()
    except RuntimeError:
        return False
    body = await request.json()
    return (
        isinstance(body, dict)
        and body.get("method") == "tools/call"
        and isinstance(body.get("params"), dict)
        and body["params"].get("name") == name
    )


async def opted_proxy_tool(params: Any, context: Any, identifier: str) -> bool:
    """逐请求协商并按显式版本确认代理组件，本地工具不进入任务代理。"""
    rc = context.request_context
    if (
        rc is None
        or rc.protocol_version not in MODERN_PROTOCOL_VERSIONS
        or context.client_extension_settings(identifier) is None
    ):
        return False
    try:
        requested_version = extract_version_spec(params.meta)
        version = VersionSpec(eq=requested_version) if requested_version else None
        return isinstance(
            await context.fastmcp.get_tool(params.name, version), ProxyTool
        )
    except NotFoundError:
        return False
