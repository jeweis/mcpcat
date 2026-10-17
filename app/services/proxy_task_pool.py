"""每来源有限的任务专属 STDIO 连接，不跨身份或任务复用进程。"""

import asyncio
from collections.abc import Callable

import anyio
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_REQUEST

from app.services.proxy_task_connection import TaskConnection


class TaskConnectionPool:
    """连接创建前占用名额，失败和取消均释放；终态连接按 TTL 保留。"""

    def __init__(self, factory: Callable, identity: Callable, limit: int = 8) -> None:
        self.factory, self.identity, self.limit = factory, identity, limit
        self.connections: set[TaskConnection] = set()

    async def open(self, owner: str) -> TaskConnection:
        """名额检查和登记之间无 await，防止并发启动突破进程限额。"""
        for connection in tuple(self.connections):
            if connection.task is not None and connection.task.done():
                await self.release(connection)
        if len(self.connections) >= self.limit:
            raise MCPError(INVALID_REQUEST, "Task connection limit exceeded")
        connection = TaskConnection(self.factory, lambda: self.identity() == owner)
        self.connections.add(connection)
        try:
            return await connection.open()
        except BaseException:
            self.connections.discard(connection)
            raise

    async def release(self, connection: TaskConnection) -> None:
        """关闭完成才释放名额，不把清理超时伪装成已释放。"""
        await connection.close()
        self.connections.discard(connection)

    async def close(self) -> None:
        """服务退出先关闭全部持有连接，取消作用域不能跳过收尾。"""
        with anyio.CancelScope(shield=True):
            await asyncio.gather(
                *(self.release(item) for item in tuple(self.connections))
            )
