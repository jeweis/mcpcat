"""封闭执行作用域并安全取消，避免工具回调等待自身父任务。"""

import asyncio

from app.services.code_mode_execution import EXECUTION_SCOPE, ExecutionScope


async def cancel_scopes(
    active: dict[asyncio.Task, ExecutionScope],
    tasks: tuple[asyncio.Task, ...],
    reason: str,
) -> None:
    """自身调用只禁止后续调度；外部调用取消并等待可清理的编排。"""
    current_scope = EXECUTION_SCOPE.get()
    waiting = []
    for task in tasks:
        scope = active.get(task)
        if scope is None:
            continue
        scope.stopped = scope.stopped or reason
        if scope is current_scope:
            continue
        task.cancel()
        waiting.append(task)
    if waiting:
        await asyncio.gather(*waiting, return_exceptions=True)
