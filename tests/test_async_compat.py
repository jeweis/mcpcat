"""真实 Python 3.10/3.11 可共同执行的超时/取消与异常组回归。"""

import asyncio
from types import SimpleNamespace

import httpx2
import pytest
from exceptiongroup import ExceptionGroup

from app.services import async_compat
from app.services.oauth_probe import _category


async def test_deadline_success() -> None:
    """正常完成不引入取消状态。"""
    async with async_compat.operation_timeout(1):
        await asyncio.sleep(0)


async def test_deadline_expires_as_timeout() -> None:
    """旧 Python 同样得到业务代码已经处理的内置超时异常。"""
    with pytest.raises(TimeoutError):
        async with async_compat.operation_timeout(0.01):
            await asyncio.Event().wait()


async def test_external_cancel_is_not_timeout() -> None:
    """外部取消不得被误归类为超时并吞掉。"""
    with pytest.raises(asyncio.CancelledError):
        async with async_compat.operation_timeout(10):
            raise asyncio.CancelledError


async def test_native_pending_cancel_is_visible() -> None:
    """3.10 走 AnyIO TaskInfo；3.11 走原生计数。"""
    task = asyncio.create_task(asyncio.sleep(10))
    task.cancel()
    assert async_compat.task_cancelling(task)
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_repeated_stop_does_not_interrupt_finally() -> None:
    """收到一次取消后正在收尾的任务，第二次停止不能打断清理。"""
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def worker() -> None:
        """清理期间明确等待测试释放。"""
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    task = asyncio.create_task(worker())
    try:
        await started.wait()
        async_compat.cancel_task_once(task)
        await cleaning.wait()
        assert async_compat.task_cancelling(task)
        async_compat.cancel_task_once(task)
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("already_pending", [False, True])
async def test_legacy_task_interface_uses_public_anyio(
    monkeypatch: pytest.MonkeyPatch, already_pending: bool
) -> None:
    """不读取 asyncio 私有字段，记录本服务在旧 Task 上发出的取消。"""

    class LegacyTask:
        """只实现 3.10 可用接口的受控 Task 替身。"""

        calls = 0

        def cancelled(self) -> bool:
            """任务仍在清理，并未结束。"""
            return False

        def done(self) -> bool:
            """模拟在途任务。"""
            return False

        def cancel(self) -> None:
            """记录取消次数。"""
            self.calls += 1

    task = LegacyTask()
    info = SimpleNamespace(
        id=id(task), has_pending_cancellation=lambda: already_pending
    )
    monkeypatch.setattr(async_compat, "get_running_tasks", lambda: [info])
    async_compat.cancel_task_once(task)
    async_compat.cancel_task_once(task)
    assert task.calls == (0 if already_pending else 1)
    assert async_compat.task_cancelling(task)


def test_backported_exception_group_preserves_oauth_category() -> None:
    """backport 和原生异常组均能提取原始安全诊断。"""
    error = ExceptionGroup("fixture", [httpx2.ConnectTimeout("fixture-only")])
    assert _category(error, []) == "timeout"
