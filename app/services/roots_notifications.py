"""将旧客户端目录变化通知转发给当前操作持有的上游会话。"""

from contextlib import asynccontextmanager
from contextvars import ContextVar
from hashlib import sha256
from typing import Any, AsyncIterator

import anyio
from fastmcp.server.dependencies import get_context, get_http_request
from fastmcp.server.middleware import Middleware
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_REQUEST
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from app.services.security_service import security_service

current_roots_relay: ContextVar[Any] = ContextVar("roots_relay", default=None)


class RootsNotifications(Middleware):
    """仅保留活跃上游连接，不缓存目录、凭证或已结束会话。"""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], set[Any]] = {}
        self.authorized = lambda request: True

    def capture(self) -> tuple[str, str] | None:
        """捕获旧协议会话及当前身份，不以仅有 session id 作为权限。"""
        try:
            context = get_context()
            request_context = context.request_context
            if request_context is None:
                return None
            if request_context.protocol_version in MODERN_PROTOCOL_VERSIONS:
                return None
            caps = context.session.client_capabilities
            if caps is None or caps.roots is None or not caps.roots.list_changed:
                return None
            request = get_http_request()
            if not self.authorized(request):
                return None
            key = request.headers.get(security_service.get_auth_header_name(), "")
            return context.session_id, sha256(key.encode()).hexdigest()
        except RuntimeError:
            return None

    @asynccontextmanager
    async def watch(self, key: tuple[str, str], session: Any) -> AsyncIterator[None]:
        """连接退出或失权时移除路由；守卫任务不超出连接作用域。"""
        request = get_http_request()
        entries = self.entries.setdefault(key, set())
        entries.add(session)
        denied = False

        async def guard(scope):
            nonlocal denied
            while True:
                if not self.authorized(request):
                    denied = True
                    scope.cancel()
                    return
                await anyio.sleep(0.25)

        try:
            async with anyio.create_task_group() as group:
                group.start_soon(guard, group.cancel_scope)
                try:
                    yield
                finally:
                    group.cancel_scope.cancel()
            if denied:
                raise MCPError(INVALID_REQUEST, "Roots connection access denied")
        finally:
            entries.discard(session)
            if not entries:
                self.entries.pop(key, None)

    async def on_request(self, context: Any, call_next: Any) -> Any:
        """将所属端点的路由器传递给本次创建的上游客户端。"""
        token = current_roots_relay.set(self)
        try:
            return await call_next(context)
        finally:
            current_roots_relay.reset(token)

    async def on_notification(self, context: Any, call_next: Any) -> Any:
        """使用 SDK 公开通知方法，不访问客户端本机文件。"""
        if context.method == "notifications/roots/list_changed":
            key = self.capture()
            for session in tuple(self.entries.get(key, ())):
                if session.protocol_version is None:
                    continue
                if session.protocol_version in MODERN_PROTOCOL_VERSIONS:
                    continue
                # 已关闭连接的发送可被 SDK 丢弃；不重连或重放工具操作。
                with anyio.move_on_after(5):
                    await session.send_roots_list_changed()
        return await call_next(context)
