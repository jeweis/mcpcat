"""STDIO 任务持有原客户端连接；上下文进入/退出始终由同一任务拥有。"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from time import monotonic
from typing import Any

import anyio
from fastmcp_tasks.client import TasksClientExtension
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_REQUEST

from app.services.async_compat import cancel_task_once


class TaskConnection:
    """有截止时间和实时权限守卫，不重连、不重新创建已提交任务。"""

    def __init__(
        self, factory: Callable, authorized: Callable, ttl: float = 3600
    ) -> None:
        self.factory, self.authorized = factory, authorized
        self.expires = monotonic() + ttl
        self.release = asyncio.Event()
        self.lock = asyncio.Lock()
        self.ready = asyncio.get_running_loop().create_future()
        self.ready.add_done_callback(
            lambda value: None if value.cancelled() else value.exception()
        )
        self.client: Any = None
        self.task: asyncio.Task | None = None

    async def open(self) -> "TaskConnection":
        """有界等待创建，调用取消或失败必须关闭已经启动的子进程。"""
        if self.task is not None:
            raise RuntimeError("Task connection already started")
        self.task = asyncio.create_task(self.run())
        try:
            await asyncio.wait_for(asyncio.shield(self.ready), 30)
            return self
        except BaseException:
            await self.close()
            raise

    def permitted(self) -> bool:
        """权限失败视为不可用，不在日志输出凭证或上游异常。"""
        try:
            return bool(self.authorized()) and monotonic() < self.expires
        except Exception:
            return False

    async def run(self) -> None:
        """唯一连接所有者负责传输上下文生命周期。"""
        client = None
        try:
            if not self.permitted():
                raise MCPError(INVALID_REQUEST, "Task connection unavailable")
            client = self.factory(extensions=[TasksClientExtension()])
            async with client:
                if not self.permitted():
                    raise MCPError(INVALID_REQUEST, "Task connection unavailable")
                self.client = client
                self.ready.set_result(None)
                while not self.release.is_set() and self.permitted():
                    try:
                        await asyncio.wait_for(self.release.wait(), 0.25)
                    except TimeoutError:
                        pass
        except Exception:
            if not self.ready.done():
                self.ready.set_exception(
                    MCPError(INVALID_REQUEST, "Task connection unavailable")
                )
        finally:
            self.client = None
            if client is not None:
                # STDIO transport 默认 keep_alive；退出会话不等于终止子进程。
                with anyio.move_on_after(5, shield=True) as cleanup:
                    await client.close()
                if cleanup.cancel_called:
                    raise RuntimeError("Task transport did not close")
            if not self.ready.done():
                self.ready.cancel()

    @asynccontextmanager
    async def use(self) -> AsyncIterator[Any]:
        """仅使用当前活连接，串行化操作；失效后不连接另一个进程。"""
        async with self.lock:
            if self.client is None or self.release.is_set() or not self.permitted():
                raise MCPError(INVALID_REQUEST, "Task connection unavailable")
            yield self.client

    async def close(self) -> None:
        """取消作用域中仍有界清理，不能由另一任务直接退出客户端上下文。"""
        self.release.set()
        if self.task is None or self.task.cancelled():
            return
        with anyio.move_on_after(5, shield=True) as grace:
            await asyncio.shield(self.task)
        if grace.cancel_called:
            cancel_task_once(self.task)
            with anyio.move_on_after(5, shield=True) as forced:
                await asyncio.gather(self.task, return_exceptions=True)
            if forced.cancel_called:
                raise RuntimeError("Task connection did not close")
