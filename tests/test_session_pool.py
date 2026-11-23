import asyncio

import pytest

from app.services.session_pool import SessionPool


class Handle:
    def __init__(self, index: int) -> None:
        self.index = index
        self.closed = False


@pytest.mark.asyncio
async def test_reuses_idle_handle_same_key():
    pool = SessionPool(max_per_key=1, idle_ttl=60)
    created = []

    async def create():
        handle = Handle(len(created))
        created.append(handle)
        return handle

    async def close(handle):
        handle.closed = True

    async with pool.acquire("k", create, close) as first:
        pass
    async with pool.acquire("k", create, close) as second:
        pass
    assert len(created) == 1
    assert first is second
    await pool.close_all(close)
    assert created[0].closed


@pytest.mark.asyncio
async def test_isolates_different_keys():
    pool = SessionPool()
    created = []

    async def create():
        handle = Handle(len(created))
        created.append(handle)
        return handle

    async def close(handle):
        handle.closed = True

    async with pool.acquire("a", create, close):
        pass
    async with pool.acquire("b", create, close):
        pass
    assert len(created) == 2


@pytest.mark.asyncio
async def test_discards_handle_on_error():
    pool = SessionPool(max_per_key=1)
    created = []

    async def create():
        handle = Handle(len(created))
        created.append(handle)
        return handle

    async def close(handle):
        handle.closed = True

    with pytest.raises(RuntimeError):
        async with pool.acquire("k", create, close):
            raise RuntimeError("boom")
    assert created[0].closed
    async with pool.acquire("k", create, close):
        pass
    assert len(created) == 2


@pytest.mark.asyncio
async def test_bounds_concurrency_per_key():
    pool = SessionPool(max_per_key=1)
    state = {"active": 0, "peak": 0}
    created = []

    async def create():
        created.append(1)
        return Handle(len(created))

    async def close(handle):
        handle.closed = True

    async def worker():
        async with pool.acquire("k", create, close):
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
            await asyncio.sleep(0.02)
            state["active"] -= 1

    await asyncio.gather(*(worker() for _ in range(3)))
    assert state["peak"] == 1
    assert len(created) == 1


@pytest.mark.asyncio
async def test_idle_ttl_expiry_creates_new():
    pool = SessionPool(max_per_key=1, idle_ttl=0.05)
    created = []

    async def create():
        created.append(1)
        return Handle(len(created))

    async def close(handle):
        handle.closed = True

    async with pool.acquire("k", create, close):
        pass
    await asyncio.sleep(0.12)
    async with pool.acquire("k", create, close):
        pass
    assert len(created) == 2
