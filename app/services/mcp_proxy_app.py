"""可热切换的 ASGI 路由边界；保持流式消息和取消，不二次写响应头。"""

import asyncio
import logging
from contextlib import nullcontext
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app.services.mcp_request_lifecycle import service_request

logger = logging.getLogger(__name__)


class MCPProxyApp:
    """按当前服务状态选定一个目标代际，该请求全程使用同一 ASGI 应用。"""

    def __init__(
        self, server_name: str, server_manager: Any, transport_type: str = "mcp"
    ) -> None:
        self.server_name = server_name
        self.server_manager = server_manager
        self.transport_type = transport_type

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """不缓冲响应；管理停止关闭流，客户端取消继续传播。"""
        started, completed = False, False
        registry = getattr(self.server_manager, "lifecycle", None)

        async def forward(message: dict) -> None:
            nonlocal started, completed
            if message["type"] in {
                "http.response.start",
                "websocket.accept",
                "websocket.close",
            }:
                started = True
            if message["type"] == "http.response.body" and not message.get(
                "more_body", False
            ):
                completed = True
            await send(message)

        try:
            target, status, message = self._target()
            if target is None:
                return await self._send_error_response(
                    scope, receive, forward, status, message
                )
            async with (
                service_request(registry, self.server_name)
                if registry
                else nullcontext()
            ):
                await target(scope, receive, forward)
        except asyncio.CancelledError:
            if (
                registry is None
                or asyncio.current_task() not in registry.stopping_requests
            ):
                raise
            await self._finish_stopped(scope, receive, forward, started, completed)
        except Exception as error:
            await self._failed(scope, receive, forward, started, error)
        finally:
            if registry is not None:
                registry.stopping_requests.discard(asyncio.current_task())

    async def _failed(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        started: bool,
        error: Exception,
    ) -> None:
        logger.error(
            "MCP ASGI 请求失败：%s (%s)", self.server_name, type(error).__name__
        )
        if started:
            raise RuntimeError("MCP 流式请求已中断") from None
        await self._send_error_response(scope, receive, send, 500, "MCP代理内部错误")

    def _target(self) -> tuple[Any, int, str]:
        info = self.server_manager.server_info.get(self.server_name)
        if info is None:
            return None, 404, "MCP服务器不存在"
        if info.get("status") != "running":
            return None, 503, "MCP服务器当前不可用"
        field = {"mcp": "mcp_app", "sse": "sse_app"}.get(self.transport_type)
        if field is None:
            return None, 500, "不支持的 MCP 传输类型"
        return info.get(field), 503, "MCP传输应用不可用"

    async def _finish_stopped(
        self, scope: Scope, receive: Receive, send: Send, started: bool, completed: bool
    ) -> None:
        if completed:
            return
        try:
            if not started:
                await self._send_error_response(
                    scope, receive, send, 503, "MCP服务正在停止，调用结果需确认"
                )
            elif scope["type"] == "http":
                await send(
                    {"type": "http.response.body", "body": b"", "more_body": False}
                )
            elif scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1012})
        except OSError:
            pass  # 对端已经断开，业务/协议任务在 target 的 finally 中完成清理。

    async def _send_error_response(
        self, scope: Scope, receive: Receive, send: Send, status_code: int, message: str
    ) -> None:
        if scope["type"] == "http":
            await JSONResponse(
                {"error": message, "server": self.server_name}, status_code=status_code
            )(scope, receive, send)
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            raise RuntimeError("Unsupported MCP ASGI scope")
