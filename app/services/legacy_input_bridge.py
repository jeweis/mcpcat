"""通过公开上下文绑定，把现代代理工具的输入轮次交给旧客户端回答。"""

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import anyio
import mcp.types as types
from fastmcp.prompts.base import InputRequiredPromptResult
from fastmcp.resources.base import InputRequiredResourceResult
from fastmcp.server.dependencies import bind_request_context, get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import InputRequiredToolResult
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from app.services.async_compat import cancel_task_once, operation_timeout

INPUT_RESULTS = (
    InputRequiredToolResult,
    InputRequiredResourceResult,
    InputRequiredPromptResult,
)


async def answer_input(session: Any, request: Any) -> Any:
    """让原客户端处理输入，不在网关执行模型或读取本机目录。"""
    if isinstance(request, types.CreateMessageRequest):
        model = (
            types.CreateMessageResultWithTools
            if request.params.tools
            else types.CreateMessageResult
        )
    elif isinstance(request, types.ElicitRequest):
        model = types.ElicitResult
        # 现代 URL 输入去掉了 elicitationId；2025-11-25 客户端仍要求它。
        # 只为旧反向请求补关联标识，不改 URL、不伪造授权完成通知。
        if (
            isinstance(request.params, types.ElicitRequestURLParams)
            and not request.params.elicitation_id
            and session.protocol_version not in MODERN_PROTOCOL_VERSIONS
        ):
            request = request.model_copy(
                update={
                    "params": request.params.model_copy(
                        update={"elicitation_id": uuid4().hex}
                    )
                }
            )
    elif isinstance(request, types.ListRootsRequest):
        model = types.ListRootsResult
    else:
        raise MCPError(types.INVALID_REQUEST, "Unsupported client input request")
    return await session.send_request(request, model)


def continuation_context(rc: Any, params: Any, method: str) -> ServerRequestContext:
    """复制公开请求字段，只为本轮注入回答和原始不透明状态。"""
    payload = params.model_dump(by_alias=True, mode="json", exclude_none=True)
    payload["_meta"] = {**(rc.meta or {}), **payload.get("_meta", {})}
    return ServerRequestContext(
        session=rc.session,
        lifespan_context=rc.lifespan_context,
        protocol_version=rc.protocol_version,
        method=method,
        params=payload,
        request_id=rc.request_id,
        request=rc.request,
        close_sse_stream=rc.close_sse_stream,
    )


class LegacyInputBridge(Middleware):
    """保持原来源调用路径和中间件校验，只恢复明确的输入请求结果。"""

    def __init__(self) -> None:
        self.authorized: Callable | None = None

    def check_access(self) -> None:
        """生产装配复用实时身份和来源代际判断。"""
        if self.authorized is not None and not self.authorized(get_http_request()):
            raise MCPError(types.INVALID_REQUEST, "Input continuation access denied")

    async def on_call_tool(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        """工具输入恢复。"""
        return await self.run(context, call_next)

    async def on_read_resource(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        """资源及资源模板输入恢复，保留原资源结果类型。"""
        return await self.run(context, call_next)

    async def on_get_prompt(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        """提示模板输入恢复，不在网关调用模型。"""
        return await self.run(context, call_next)

    async def answer(self, session: Any, request: Any) -> Any:
        """等待期间校验权限，撤销即取消原客户端的等待请求。"""
        self.check_access()
        pending = asyncio.create_task(answer_input(session, request))
        try:
            while True:
                done, _ = await asyncio.wait({pending}, timeout=0.25)
                self.check_access()
                if done:
                    return pending.result()
        finally:
            cancel_task_once(pending)
            with anyio.move_on_after(5, shield=True) as cleanup:
                await asyncio.gather(pending, return_exceptions=True)
            if cleanup.cancel_called:
                raise RuntimeError("Client input cleanup did not complete")

    async def run(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        """有限轮次/时间内桥接旧客户端；普通成功/失败结果绝不重试。"""
        ctx = context.fastmcp_context
        rc = ctx.request_context if ctx else None
        if rc is None or rc.protocol_version in MODERN_PROTOCOL_VERSIONS:
            return await call_next(context)
        async with operation_timeout(300):
            result = await call_next(context)
            for round_number in range(33):
                if not isinstance(result, INPUT_RESULTS):
                    return result
                self.check_access()
                if round_number == 32:
                    raise MCPError(types.INVALID_REQUEST, "Input round limit exceeded")
                ask = result.input_required
                requests = ask.input_requests or {}
                if (
                    not requests
                    or len(requests) > 16
                    or len(ask.model_dump_json().encode()) > 65536
                ):
                    raise MCPError(types.INVALID_REQUEST, "Invalid input request count")
                responses = {
                    key: await self.answer(rc.session, request)
                    for key, request in requests.items()
                }
                params = context.message.model_copy(
                    update={
                        "input_responses": responses,
                        "request_state": ask.request_state,
                    }
                )
                if len(params.model_dump_json().encode()) > 65536:
                    raise MCPError(
                        types.INVALID_REQUEST, "Input response limit exceeded"
                    )
                self.check_access()
                with bind_request_context(
                    continuation_context(rc, params, context.method)
                ):
                    result = await call_next(context.copy(message=params))
                self.check_access()
