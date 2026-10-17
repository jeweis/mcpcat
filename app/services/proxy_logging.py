"""保留上游日志 JSON，复用 SDK 投递与框架公开会话状态。"""

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

from anyio.abc import ObjectReceiveStream
from fastmcp.client.logging import LogMessage
from fastmcp.server.dependencies import get_context
from fastmcp.server.middleware import Middleware
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
from mcp.shared.message import SessionMessage
from mcp.shared.exceptions import MCPError
from mcp_types import JSONRPCNotification, LoggingMessageNotificationParams
from pydantic import BaseModel, ValidationError, model_serializer

LEVELS = (
    "debug",
    "info",
    "notice",
    "warning",
    "error",
    "critical",
    "alert",
    "emergency",
)
current_log_level: ContextVar[Callable[[], str | None] | None] = ContextVar(
    "proxy_log_level", default=None
)


class NullLogData(BaseModel):
    """让 SDK exclude_none 保留必需 data 字段，JSON 值仍为 null。"""

    @model_serializer
    def serialize_null(self) -> None:
        """仅在 JSON 序列化时输出 null，不改变 SDK 的投递与过滤。"""
        return None


class OrderedLogStream(ObjectReceiveStream):
    """先完成线序中的日志投递，再将后续输入请求交给 SDK 并发分发。"""

    def __init__(self, stream: Any, handler: Any) -> None:
        self.stream, self.handler = stream, handler

    async def receive(self) -> Any:
        """保留原消息供 SDK 校验和 message handler 观察，不复制握手。"""
        item = await self.stream.receive()
        message = item.message if isinstance(item, SessionMessage) else None
        if (
            isinstance(message, JSONRPCNotification)
            and message.method == "notifications/message"
        ):
            try:
                params = LoggingMessageNotificationParams.model_validate(message.params)
            except ValidationError:
                return item
            await self.handler(params)
        return item

    async def aclose(self) -> None:
        """关闭原接收流。"""
        await self.stream.aclose()


async def already_forwarded_log(message: LogMessage) -> None:
    """有序接收流已转发日志；SDK 回调不重复发送或写应用日志。"""


class ProxyLogging(Middleware):
    """记住已成功的旧 setLevel；不接管协议解析、校验或请求分发。"""

    def __init__(self, max_sessions: int = 4096) -> None:
        self.levels: dict[str, str] = {}
        self.max_sessions = max_sessions
        self.setting = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(self, server: Any):
        """阈值跟随端点实例，不以缓存 TTL 改变仍有效的旧会话。"""
        try:
            yield {}
        finally:
            self.levels.clear()

    async def on_request(self, context: Any, call_next: Any) -> Any:
        """在原请求上下文读取阈值，避免异步日志回调串到上游上下文。"""
        ctx = get_context()
        request = ctx.request_context
        legacy = (
            request is not None
            and request.protocol_version not in MODERN_PROTOCOL_VERSIONS
        )
        key = ctx.session_id if legacy else ""
        default = ctx.fastmcp.client_log_level
        token = current_log_level.set(
            (lambda: self.levels.get(key, default)) if legacy else (lambda: default)
        )
        try:
            if legacy and context.method == "logging/setLevel":
                async with self.setting:
                    if key not in self.levels and len(self.levels) >= self.max_sessions:
                        raise MCPError(-32000, "Logging session limit exceeded")
                    result = await call_next(context)
                    params = context.message
                    value = (
                        params.get("level")
                        if isinstance(params, dict)
                        else params.level
                    )
                    self.levels[key] = value
                    return result
            return await call_next(context)
        finally:
            current_log_level.reset(token)


def request_log_handler(context: Any = None) -> Any:
    """捕获原请求的公开会话，不将任意日志内容写入网关应用日志。"""
    try:
        ctx = context if context is not None else get_context()
        session = ctx.session
        request_id = ctx.origin_request_id
        default = ctx.fastmcp.client_log_level
        get_level = current_log_level.get() or (lambda: default)
    except RuntimeError:
        session = None
        request_id = None
        get_level = lambda: None

    async def relay(message: LogMessage) -> None:
        if session is None:
            return
        level = get_level()
        if level is not None and LEVELS.index(message.level) < LEVELS.index(level):
            return
        await session.send_log_message(
            level=message.level,
            data=NullLogData() if message.data is None else message.data,
            logger=message.logger,
            related_request_id=request_id,
        )

    return relay
