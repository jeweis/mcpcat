"""连接启动前占位；取消或失败不能留下可无限增长的持有任务。"""

import asyncio

import pytest
from mcp.shared.exceptions import MCPError

from app.services.proxy_task_pool import TaskConnectionPool


class WaitingClient:
    def __init__(self, started, proceed, closed):
        self.started, self.proceed, self.closed = started, proceed, closed

    async def __aenter__(self):
        self.started.set()
        await self.proceed.wait()
        return self

    async def __aexit__(self, *error):
        return False

    async def close(self):
        self.closed.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_pool_bounds_pending_creation_and_cleans_up(cancel):
    started, proceed, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    clients = []

    def factory(**options):
        client = WaitingClient(started, proceed, closed)
        clients.append(client)
        return client

    pool = TaskConnectionPool(factory, lambda: "owner", limit=1)
    async with asyncio.timeout(15):
        creating = asyncio.create_task(pool.open("owner"))
        try:
            await started.wait()
            with pytest.raises(MCPError, match="connection limit"):
                await pool.open("owner")
            assert len(clients) == 1
            if cancel:
                creating.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await creating
                assert closed.is_set()
                assert not pool.connections
            else:
                proceed.set()
                connection = await creating
                await pool.release(connection)
                assert closed.is_set() and not pool.connections
                # 只有释放成功才能创建下一个连接。
                await pool.open("owner")
                assert len(clients) == 2
        finally:
            proceed.set()
            await pool.close()
            await asyncio.gather(creating, return_exceptions=True)
    assert not pool.connections
