"""按 (server, 下游会话) 复用已建立的上游连接：独占租用、空闲回收、错误即弃。

P2 基础组件。真正的代理接线还需要把“按请求绑定”的回调改为“按连接动态派发”，
本模块只负责连接句柄的生命周期与复用，便于单测与后续接入。
"""

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List


class _Idle:
    __slots__ = ("handle", "last_used")

    def __init__(self, handle: Any) -> None:
        self.handle = handle
        self.last_used = time.monotonic()


async def _safe_close(close: Callable[[Any], Awaitable[None]], handle: Any) -> None:
    try:
        await close(handle)
    except Exception:
        pass


class SessionPool:
    """按 key 独占复用连接；超过并发上限时等待；空闲超时关闭。

    调用方提供 create（返回已建立的连接句柄）和 close（关闭句柄）。
    出错时丢弃连接，不把坏连接放回复用池。
    """

    def __init__(
        self,
        *,
        max_per_key: int = 2,
        idle_ttl: float = 60.0,
        max_keys: int = 256,
    ) -> None:
        self._max_per_key = max(1, max_per_key)
        self._idle_ttl = idle_ttl
        self._max_keys = max(1, max_keys)
        self._idle: Dict[Any, List[_Idle]] = {}
        self._sems: Dict[Any, asyncio.Semaphore] = {}
        self._lock = asyncio.Lock()

    def _sem(self, key: Any) -> asyncio.Semaphore:
        sem = self._sems.get(key)
        if sem is None:
            sem = asyncio.Semaphore(self._max_per_key)
            self._sems[key] = sem
        return sem

    def _prune(self, key: Any, close: Callable[[Any], Awaitable[None]]) -> None:
        now = time.monotonic()
        keep: List[_Idle] = []
        for slot in self._idle.get(key, []):
            if now - slot.last_used <= self._idle_ttl:
                keep.append(slot)
            else:
                asyncio.create_task(_safe_close(close, slot.handle))
        self._idle[key] = keep

    @asynccontextmanager
    async def acquire(
        self,
        key: Any,
        create: Callable[[], Awaitable[Any]],
        close: Callable[[Any], Awaitable[None]],
    ) -> AsyncIterator[Any]:
        """独占租用一条连接；正常退出归还复用，异常退出关闭丢弃。"""
        sem = self._sem(key)
        await sem.acquire()
        handle: Any = None
        try:
            async with self._lock:
                self._prune(key, close)
                idle = self._idle.get(key) or []
                if idle:
                    handle = idle.pop().handle
            if handle is None:
                handle = await create()
            yield handle
        except BaseException:
            if handle is not None:
                await _safe_close(close, handle)
            handle = None
            raise
        finally:
            if handle is not None:
                async with self._lock:
                    self._idle.setdefault(key, []).append(_Idle(handle))
            sem.release()

    async def close_all(self, close: Callable[[Any], Awaitable[None]]) -> None:
        """关闭所有空闲连接（进程退出/测试清理）。"""
        async with self._lock:
            slots = [slot for lst in self._idle.values() for slot in lst]
            self._idle.clear()
        for slot in slots:
            await _safe_close(close, slot.handle)
