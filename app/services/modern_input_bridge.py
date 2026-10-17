"""现代请求恢复仍在运行的旧上游调用；只保存有界的临时身份路由。"""

import asyncio
import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import uuid4

import anyio
import mcp.types as types
from fastmcp.prompts.base import InputRequiredPromptResult
from fastmcp.resources.base import InputRequiredResourceResult
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import InputRequiredToolResult
from mcp.shared.exceptions import MCPError
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from app.services.input_exchange import InputExchange, current_exchange
from app.services.proxy_logging import request_log_handler
from app.services.security_service import security_service

PREFIX = "mcpcat-input-v1:"


def request_identity() -> str:
    """仅保留凭证摘要；无 HTTP 的本地公共调用不拥有独立 Key 身份。"""
    try:
        key = get_http_request().headers.get(
            security_service.get_auth_header_name(), ""
        )
    except RuntimeError:
        key = ""
    return sha256(key.encode()).hexdigest()


def invocation_identity(context: MiddlewareContext) -> str:
    """恢复必须保持方法、组件、参数及显式组件版本，不保存原参数。"""
    payload = context.message.model_dump(by_alias=True, mode="json", exclude_none=True)
    meta = payload.pop("_meta", {})
    payload.pop("inputResponses", None)
    payload.pop("requestState", None)
    value = [context.method, payload, meta.get("fastmcp")]
    return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@dataclass
class PendingInput:
    owner: str
    invocation: str
    exchange: InputExchange


class ModernInputBridge(Middleware):
    """原调用归属当前服务实例，终止或失效后不重建旧操作。"""

    def __init__(self) -> None:
        self.authorized: Callable | None = None
        self.pending: dict[str, PendingInput] = {}
        self.alive = False
        self.started = False

    @asynccontextmanager
    async def lifespan(self, server: Any):
        """端点停服取消原调用及连接，不能遗留到新代际。"""
        self.started = True
        self.alive = True
        try:
            yield {}
        finally:
            self.alive = False
            with anyio.CancelScope(shield=True):
                await asyncio.gather(
                    *(item.exchange.close() for item in list(self.pending.values()))
                )
            self.pending.clear()

    def permitted(self) -> bool:
        """当前请求与后台持有的原请求都复用同一实时授权谓词。"""
        return self.alive and (
            self.authorized is None or self.authorized(get_http_request())
        )

    async def on_call_tool(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        """工具调用保留原组件分发。"""
        return await self.run(context, call_next, InputRequiredToolResult)

    async def on_read_resource(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        """资源读取保留原 MIME 和结果类型。"""
        return await self.run(context, call_next, InputRequiredResourceResult)

    async def on_get_prompt(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        """提示获取保留原消息结果。"""
        return await self.run(context, call_next, InputRequiredPromptResult)

    def acquire(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> tuple[str, PendingInput]:
        """校验恢复身份及参数；新操作在启动前占用名额。"""
        if not self.permitted():
            raise MCPError(types.INVALID_REQUEST, "Input continuation access denied")
        owner, invocation = request_identity(), invocation_identity(context)
        state = context.fastmcp_context.request_state or ""
        if state.startswith(PREFIX):
            key = state[len(PREFIX) :]
            item = self.pending.get(key)
            if item is None or item.owner != owner or item.invocation != invocation:
                raise MCPError(types.INVALID_REQUEST, "Input continuation unavailable")
            responses = context.fastmcp_context.input_responses or {}
            if set(responses) != {item.exchange.question_id}:
                raise MCPError(types.INVALID_REQUEST, "Input question unavailable")
            question_id, response = next(iter(responses.items()))
            item.exchange.respond(question_id, response)
            item.exchange.progress_handler = (
                context.fastmcp_context.session.report_progress
            )
            item.exchange.log_handler = request_log_handler(context.fastmcp_context)
            return key, item
        if len(self.pending) >= 32:
            raise MCPError(types.INVALID_REQUEST, "Input continuation limit exceeded")
        key = uuid4().hex
        exchange = InputExchange(self.permitted)
        exchange.progress_handler = context.fastmcp_context.session.report_progress
        exchange.log_handler = request_log_handler(context.fastmcp_context)
        item = PendingInput(owner, invocation, exchange)
        self.pending[key] = item
        exchange.start(lambda: call_next(context))
        exchange.task.add_done_callback(lambda task: self.pending.pop(key, None))
        return key, item

    async def run(
        self, context: MiddlewareContext, call_next: CallNext, wrapper: Any
    ) -> Any:
        """返回输入请求时持有旧操作，恢复后只交付其真实结果。"""
        ctx = context.fastmcp_context
        rc = ctx.request_context if ctx else None
        if (
            rc is None
            or rc.protocol_version not in MODERN_PROTOCOL_VERSIONS
            or current_exchange.get() is not None
        ):
            return await call_next(context)
        # FastMCP 的公开组件调用可以不启动独立 lifespan（如 Catalog 读取）。
        # 此时保留原同步分发，但不能创建或接管需要服务生命周期的恢复路由。
        if not self.started and not (ctx.request_state or "").startswith(PREFIX):
            return await call_next(context)
        key, item = self.acquire(context, call_next)
        retained = False
        try:
            await item.exchange.flush_logs()
            kind, value = await item.exchange.events.get()
            if not self.permitted():
                raise MCPError(
                    types.INVALID_REQUEST, "Input continuation access denied"
                )
            if kind == "error":
                raise value
            if kind == "input":
                question_id, request = value
                result = wrapper(
                    types.InputRequiredResult(
                        input_requests={question_id: request},
                        request_state=PREFIX + key,
                    )
                )
                retained = True
                return result
            return value
        finally:
            await item.exchange.detach_request()
            if not retained:
                await item.exchange.close()
                self.pending.pop(key, None)
