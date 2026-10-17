"""REST 预览请求的断开取消，不把取消宣称为工具副作用回滚。"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from starlette.requests import Request

from app.services.app_content_service import AppDomainError

T = TypeVar("T")
_retained: set[asyncio.Future] = set()


def _consume(task: asyncio.Future) -> None:
    """保留不合作的收尾直到完成，并读取异常避免未观察任务泄漏。"""
    _retained.discard(task)
    if not task.cancelled():
        task.exception()


async def _disconnect(request: Request, stopped: asyncio.Event) -> None:
    # 请求体已由 FastAPI 解析。必须阻塞等待 receive：is_disconnected() 的
    # 立即取消探测在 BaseHTTPMiddleware 的内存通道中可能一直读不到断开。
    while not stopped.is_set():
        if (await request.receive())["type"] == "http.disconnect":
            return


async def _cleanup(tasks: tuple[asyncio.Future, ...], timeout: float) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in done:
        _consume(task)
    for task in pending:
        _retained.add(task)
        task.add_done_callback(_consume)


async def run_preview_request(
    request: Request,
    operation: Callable[[], Awaitable[T]],
    *,
    cleanup_timeout: float = 1.0,
) -> T:
    """连接关闭或父请求取消后不再等待结果，给可取消任务有界收尾。"""
    if not 0 < cleanup_timeout <= 1:
        raise ValueError("预览清理时间必须在 0 到 1 秒之间")
    if await request.is_disconnected():
        raise AppDomainError("预览连接已关闭，不会开始工具调用", 499)
    call = asyncio.ensure_future(operation())
    stopped = asyncio.Event()
    disconnect = asyncio.create_task(_disconnect(request, stopped))
    try:
        done, _ = await asyncio.wait(
            (call, disconnect), return_when=asyncio.FIRST_COMPLETED
        )
        if call in done:
            return await call
        await disconnect
        raise AppDomainError("预览连接已关闭；部分操作可能已完成，请勿自动重试", 499)
    finally:
        stopped.set()
        await _cleanup((call, disconnect), cleanup_timeout)
