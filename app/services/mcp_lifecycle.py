"""动态 MCP 生命周期：每个上下文在自己的任务内进入/退出，不操作 SDK 私有状态。"""

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import wraps
from typing import Any, AsyncIterator, Awaitable, Callable
from weakref import WeakValueDictionary

from app.services.async_compat import cancel_task_once, task_cancelling
from app.services.mcp_request_lifecycle import cancel_service_requests

logger = logging.getLogger(__name__)
START_TIMEOUT = 10
STOP_TIMEOUT = 5


def service_operation(position: int = 1, keyword: str = "server_name") -> Callable:
    """按来源串行管理操作；同一任务内 restart→stop/start 可重入。"""

    def decorate(function: Callable[..., Awaitable[Any]]) -> Callable:
        @wraps(function)
        async def wrapped(manager: Any, *args: Any, **kwargs: Any) -> Any:
            name = args[position - 1] if len(args) >= position else kwargs[keyword]
            async with manager.lifecycle.operation(name):
                return await function(manager, *args, **kwargs)

        return wrapped

    return decorate


@dataclass
class LifecycleHandle:
    """一个明确代际的应用、就绪 Future 与任务。"""

    info: dict
    application: Any
    ready: asyncio.Future[bool]
    task: asyncio.Task | None = None
    failed: bool = False
    started: bool = False


class ServiceLifecycles:
    """统一管理初始和动态服务；失败/超时不得隐瞒为已启动或已清理。"""

    def __init__(self, manager: Any) -> None:
        self.manager = manager
        self.handles: dict[str, LifecycleHandle] = {}
        self.locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self.owners: dict[str, asyncio.Task] = {}
        self.background: dict[asyncio.Task, str | None] = {}
        self.requests: dict[str, set[asyncio.Task]] = {}
        self.stopping_requests: set[asyncio.Task] = set()
        self.closing = False

    @asynccontextmanager
    async def operation(self, name: str) -> AsyncIterator[None]:
        """只序列化同一来源的管理操作，不阻塞其他来源的正常请求。"""
        owner = asyncio.current_task()
        if self.owners.get(name) is owner:
            yield
            return
        lock = self.locks.setdefault(name, asyncio.Lock())
        async with lock:
            self.owners[name] = owner
            try:
                yield
            finally:
                self.owners.pop(name, None)

    def _current(self, name: str, handle: LifecycleHandle) -> bool:
        info = self.manager.server_info.get(name)
        return info is handle.info and info.get("mcp_app") is handle.application

    async def _run(
        self, name: str, handle: LifecycleHandle, context: Any, app: Any
    ) -> None:
        try:
            async with context(app):
                if not self._current(name, handle):
                    return
                handle.started = True
                self.manager._update_server_status(name, "running")
                handle.ready.set_result(True)
                await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            handle.failed = True
            logger.error("MCP 生命周期失败：%s (%s)", name, type(error).__name__)
            if self._current(name, handle):
                self.manager._update_server_status(
                    name, "failed", "MCP 生命周期启动或关闭失败"
                )
        finally:
            if not handle.ready.done():
                handle.ready.set_result(False)
            if self._current(name, handle) and not handle.failed:
                self.manager._update_server_status(name, "stopped")

    async def start(self, name: str, app: Any) -> bool:
        """等待真实 lifespan 就绪，不以固定 sleep 或任务存在作为启动证据。"""
        async with self.operation(name):
            info = self.manager.server_info.get(name)
            if info is None:
                return False
            if self.closing:
                return False
            handle = self.handles.get(name)
            if handle and handle.failed and handle.started:
                return False
            if handle and handle.task and not handle.task.done():
                if not self._current(name, handle) or task_cancelling(handle.task):
                    return False
                ready = await asyncio.shield(handle.ready)
                if ready:
                    self.manager._update_server_status(name, "running")
                return ready
            context = self.manager.lifespan_tasks[name]
            ready = asyncio.get_running_loop().create_future()
            handle = LifecycleHandle(info, info["mcp_app"], ready)
            self.handles[name] = handle
            self.manager._update_server_status(name, "starting")
            handle.task = asyncio.create_task(
                self._run(name, handle, context, app), name=f"mcpcat:service:{name}"
            )
            self.manager.dynamic_tasks.add(handle.task)
            handle.task.add_done_callback(self.manager.dynamic_tasks.discard)
            try:
                return await asyncio.wait_for(asyncio.shield(ready), START_TIMEOUT)
            except asyncio.CancelledError:
                await self.stop(name)
                raise
            except (TimeoutError, asyncio.TimeoutError):
                await self.stop(name)
                self.manager._update_server_status(
                    name, "failed", "MCP 生命周期启动超时"
                )
                return False

    async def stop(self, name: str) -> bool:
        """初始/动态实例走相同关闭路径；未完成任务仍留在记录中。"""
        async with self.operation(name):
            await self.cancel_background(name)
            if not await cancel_service_requests(self, name):
                return False
            handle = self.handles.get(name)
            if not handle or not handle.task:
                return True
            task = handle.task
            if not task.done():
                cancel_task_once(task)
                _, pending = await asyncio.wait({task}, timeout=STOP_TIMEOUT)
                if pending:
                    self.manager._update_server_status(
                        name, "failed", "MCP 生命周期关闭超时"
                    )
                    return False
            self.manager.dynamic_tasks.discard(task)
            if not handle.ready.done():
                handle.ready.set_result(False)
            if handle.failed and handle.started:
                self.manager._update_server_status(
                    name, "failed", "MCP 生命周期退出失败"
                )
                return False
            if self.handles.get(name) is handle:
                self.handles.pop(name, None)
            return True

    def schedule(
        self, awaitable: Awaitable[Any], name: str | None = None
    ) -> asyncio.Task:
        """跟踪授权探测/Catalog 刷新，关闭时不留下匿名后台任务。"""
        task = asyncio.create_task(
            awaitable, name=f"mcpcat:background:{name or 'catalog'}"
        )
        self.background[task] = name
        task.add_done_callback(self._background_done)
        if self.closing:
            cancel_task_once(task)
        return task

    def _background_done(self, task: asyncio.Task) -> None:
        self.background.pop(task, None)
        if not task.cancelled() and task.exception() is not None:
            logger.warning("MCP 后台任务失败 (%s)", type(task.exception()).__name__)

    async def cancel_background(self, name: str | None = None) -> None:
        """按来源或整体取消辅助任务；取消传播到 HTTP 客户端。"""
        tasks = {
            task
            for task, owner in self.background.items()
            if name is None or owner == name
        }
        for task in tasks:
            cancel_task_once(task)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=STOP_TIMEOUT)
            if pending:
                raise RuntimeError("MCP 后台任务未完成关闭")

    async def close(self) -> None:
        """应用退出时关闭所有代际，未完成清理则明确失败。"""
        self.closing = True
        for name, handle in self.handles.items():
            if self._current(name, handle):
                self.manager._update_server_status(name, "stopping")
        await self.cancel_background()
        results = await asyncio.gather(
            *(self.stop(name) for name in list(self.handles))
        )
        if not all(results):
            raise RuntimeError("部分 MCP 生命周期未完成关闭")
