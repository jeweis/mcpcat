"""MCP HTTP/SSE 响应禁止共享 HTTP 缓存；不修改协议层客户端缓存字段。"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.services.security_service import security_service


class MCPPrivateCacheMiddleware:
    """覆盖正常/错误/流式响应头，不缓冲响应正文。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """HTTP 认证错误也必须经过此层；SSE 流逐块透传。"""
        if scope["type"] != "http" or not scope.get("path", "").startswith(
            ("/mcp/", "/sse/")
        ):
            await self.app(scope, receive, send)
            return

        async def private_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = list(message.get("headers", []))
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "private, no-store"
                vary = headers.get("Vary", "")
                name = security_service.get_auth_header_name()
                if name.lower() not in {
                    part.strip().lower() for part in vary.split(",")
                }:
                    headers["Vary"] = ", ".join(part for part in (vary, name) if part)
            await send(message)

        await self.app(scope, receive, private_send)
