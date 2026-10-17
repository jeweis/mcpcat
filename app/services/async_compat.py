"""Python 3.10+ 的超时和取消查询，复用 AnyIO 公共兼容接口。"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator
from weakref import WeakSet

from anyio import fail_after, get_running_tasks

_cancel_requested: WeakSet[asyncio.Task] = WeakSet()


@asynccontextmanager
async def operation_timeout(seconds: float) -> AsyncIterator[None]:
    """有限操作期限，不依赖 Python 3.11 的 asyncio.timeout。"""
    with fail_after(seconds):
        yield


def task_cancelling(task: asyncio.Task) -> bool:
    """3.11 使用原生计数；3.10 保留请求历史并查询 AnyIO 当前取消状态。"""
    native = getattr(task, "cancelling", None)
    if native is not None:
        return bool(native())
    if task in _cancel_requested or task.cancelled():
        return True
    return any(
        info.id == id(task) and info.has_pending_cancellation()
        for info in get_running_tasks()
    )


def cancel_task_once(task: asyncio.Task) -> None:
    """重复停止不再次取消正在收尾的任务，避免打断其 finally。"""
    if task.done() or task_cancelling(task):
        return
    if getattr(task, "cancelling", None) is None:
        _cancel_requested.add(task)
    task.cancel()
