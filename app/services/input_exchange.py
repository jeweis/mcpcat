"""持有一次旧协议调用，把客户端输入转成可等待的多轮交换，不重放操作。"""

import asyncio
import json
from collections import deque
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

import anyio
import mcp.types as types
from mcp.shared.exceptions import MCPError

from app.services.async_compat import cancel_task_once, operation_timeout

current_exchange: ContextVar["InputExchange | None"] = ContextVar(
    "mcpcat_input_exchange", default=None
)


class InputExchange:
    """单调用、单问题窗口；原操作与所有客户端连接在同一任务内存活。"""

    def __init__(self, authorized: Callable[[], bool], timeout: float = 300) -> None:
        self.authorized, self.timeout = authorized, timeout
        self.events: asyncio.Queue = asyncio.Queue(maxsize=1)
        self.answer_future: asyncio.Future | None = None
        self.question_id: str | None = None
        self.answer_type: type | None = None
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.delivery_lock = asyncio.Lock()
        self.rounds = 0
        self.progress_handler: Callable | None = None
        self.log_handler: Callable | None = None
        self.pending_logs: deque = deque()
        self.log_bytes = 0
        self.closed = False

    async def report_log(self, message: Any) -> None:
        """跨旧协议通道迟到的日志仅保留在原调用内，恢复后交付。"""
        async with self.delivery_lock:
            self.check_access()
            if self.closed:
                return
            payload = (
                message.model_dump(mode="json")
                if hasattr(message, "model_dump")
                else message
            )
            size = len(json.dumps(payload, ensure_ascii=False).encode())
            if len(self.pending_logs) >= 200 or self.log_bytes + size > 262144:
                raise MCPError(types.INVALID_REQUEST, "Input log buffer limit exceeded")
            self.pending_logs.append((message, size))
            self.log_bytes += size
            await self.deliver_logs()

    async def deliver_logs(self) -> None:
        """调用者持有投递锁；每条日志重新检查授权与当前回调。"""
        while self.pending_logs and self.log_handler is not None:
            self.check_access()
            message, size = self.pending_logs.popleft()
            self.log_bytes -= size
            await self.log_handler(message)

    async def flush_logs(self) -> None:
        """恢复请求交付输入结果前排空原调用日志，不创建替代调用。"""
        async with self.delivery_lock:
            self.check_access()
            await self.deliver_logs()

    async def detach_request(self) -> None:
        """原响应结束前等待在途投递，随后日志只能暂存给恢复请求。"""
        async with self.delivery_lock:
            self.progress_handler = None
            self.log_handler = None

    async def report_progress(
        self, progress: float, total: float | None, message: str | None
    ) -> None:
        """仅向当前有效恢复请求转发进度，不复用已结束的请求通道。"""
        async with self.delivery_lock:
            self.check_access()
            handler = self.progress_handler
            if handler is not None:
                await handler(progress, total, message)

    def check_access(self) -> None:
        """身份或来源代际失效时拒绝继续原操作。"""
        if not self.authorized():
            raise MCPError(types.INVALID_REQUEST, "Input exchange access denied")

    def start(self, operation: Callable) -> None:
        """只能启动一次，恢复回答不调用 operation。"""
        if self.task is not None:
            raise RuntimeError("Input exchange already started")
        self.task = asyncio.create_task(self.run(operation))

    async def run(self, operation: Callable) -> None:
        """独立取消范围持有旧调用；到期或显式关闭仍会清理。"""
        token = current_exchange.set(self)
        try:
            with anyio.CancelScope(shield=True):
                async with operation_timeout(self.timeout):
                    self.check_access()
                    result = await operation()
                    self.check_access()
                    async with self.delivery_lock:
                        await self.events.put(("result", result))
        except asyncio.CancelledError:
            if self.events.full():
                self.events.get_nowait()
            self.events.put_nowait(
                ("error", MCPError(types.INVALID_REQUEST, "Input exchange closed"))
            )
            raise
        except Exception as error:
            # 客户端可能已离开而未取走问题；终态不能被满队列无限阻塞。
            if self.events.full():
                self.events.get_nowait()
            self.events.put_nowait(("error", error))
        finally:
            current_exchange.reset(token)

    async def ask(self, request: Any) -> Any:
        """串行化问题并持有原回调，响应只能提交给当前问题一次。"""
        async with self.lock:
            self.check_access()
            self.rounds += 1
            if self.rounds > 32 or len(request.model_dump_json().encode()) > 65536:
                raise MCPError(types.INVALID_REQUEST, "Input exchange limit exceeded")
            if isinstance(request, types.ListRootsRequest):
                self.answer_type = types.ListRootsResult
            elif isinstance(request, types.ElicitRequest):
                self.answer_type = types.ElicitResult
            elif isinstance(request, types.CreateMessageRequest):
                self.answer_type = (
                    types.CreateMessageResultWithTools
                    if request.params.tools
                    else types.CreateMessageResult
                )
            else:
                raise MCPError(
                    types.INVALID_REQUEST, "Unsupported client input request"
                )
            self.question_id = uuid4().hex
            self.answer_future = asyncio.get_running_loop().create_future()
            try:
                async with self.delivery_lock:
                    await self.events.put(("input", (self.question_id, request)))
                while True:
                    done, _ = await asyncio.wait({self.answer_future}, timeout=0.25)
                    self.check_access()
                    if done:
                        return self.answer_future.result()
            finally:
                if not self.answer_future.done():
                    self.answer_future.cancel()
                self.answer_future = None
                self.question_id = None
                self.answer_type = None

    def respond(self, question_id: str, response: Any) -> None:
        """拒绝过期、重复及超大回答；不保存已消费的回答。"""
        self.check_access()
        if (
            question_id != self.question_id
            or self.answer_future is None
            or self.answer_future.done()
        ):
            raise MCPError(types.INVALID_REQUEST, "Input question unavailable")
        if len(response.model_dump_json().encode()) > 65536:
            raise MCPError(types.INVALID_REQUEST, "Input response limit exceeded")
        if self.answer_type is None or not isinstance(response, self.answer_type):
            raise MCPError(types.INVALID_REQUEST, "Input response type mismatch")
        self.answer_future.set_result(response)

    async def close(self) -> None:
        """在取消作用域中仍等待原操作释放连接，不创建替代操作。"""
        self.progress_handler = None
        self.log_handler = None
        self.closed = True
        self.pending_logs.clear()
        self.log_bytes = 0
        if self.task is None:
            return
        cancel_task_once(self.task)
        with anyio.move_on_after(5, shield=True) as cleanup:
            await asyncio.gather(self.task, return_exceptions=True)
        if cleanup.cancel_called:
            raise RuntimeError("Input exchange cleanup did not complete")
