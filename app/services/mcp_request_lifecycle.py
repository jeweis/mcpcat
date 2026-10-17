"""管理来源的活动 ASGI 请求；停止时等待取消清理，不伪装业务回滚。"""

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from app.services.async_compat import cancel_task_once


@asynccontextmanager
async def service_request(registry: Any, name: str) -> AsyncIterator[None]:
    """一个请求只登记当前任务，退出时移除；不读取或缓存报文。"""
    task = asyncio.current_task()
    tasks = registry.requests.setdefault(name, set())
    tasks.add(task)
    try:
        yield
    finally:
        tasks.discard(task)
        if not tasks and registry.requests.get(name) is tasks:
            registry.requests.pop(name, None)


async def cancel_service_requests(registry: Any, name: str) -> bool:
    """取消可取消请求；超时仍保留记录并拒绝宣称服务已停止。"""
    tasks = set(registry.requests.get(name, set())) - {asyncio.current_task()}
    for task in tasks:
        registry.stopping_requests.add(task)
        cancel_task_once(task)
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=5)
        if pending:
            registry.manager._update_server_status(
                name, "failed", "MCP 请求尚未完成清理"
            )
            return False
    return True
