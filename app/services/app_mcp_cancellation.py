"""Apps JSON 传输的断连清理；复用有界收尾，不修改 SDK 私有会话。"""

import asyncio
from typing import Any

from fastmcp.resources import ResourceResult
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult

from app.services.app_content_service import AppDomainError
from app.services.app_preview_cancellation import run_preview_request


async def _request_operation(context: MiddlewareContext, call_next: CallNext) -> Any:
    # 此处已经解析完成 MCP 请求体，不在 ASGI 边界与 SDK 争抢请求正文。
    # 仅补充现代 JSON 传输；不读取报文正文或改写 SDK 的旧会话取消行为。
    try:
        request = get_http_request()
    except RuntimeError:
        return await call_next(context)
    # 旧版有原生 notifications/cancelled；不重复接管它的会话/流清理。
    if request.headers.get("mcp-protocol-version") != "2026-07-28":
        return await call_next(context)
    try:
        return await run_preview_request(request, lambda: call_next(context))
    except AppDomainError as error:
        if error.status_code == 499:
            raise asyncio.CancelledError(
                "App client disconnected; outcome unknown"
            ) from None
        raise


class AppDisconnectMiddleware(Middleware):
    """仅 Apps 启用的 JSON 端点使用；保留普通部署的原传输与原协议。"""

    async def on_call_tool(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> ToolResult:
        """客户端断开后取消可取消工具，不重试或宣称副作用回滚。"""
        return await _request_operation(context, call_next)

    async def on_read_resource(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> ResourceResult:
        """大资源装配/读取也随请求结束收尾，内存客户端不增加 HTTP 前提。"""
        return await _request_operation(context, call_next)
