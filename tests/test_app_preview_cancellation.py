"""断开/父任务取消的收尾，不声称终止不合作的业务工具。"""

import asyncio

import pytest
from starlette.requests import Request

from app.services.app_content_service import AppDomainError
from app.services.app_preview_cancellation import _retained, run_preview_request


def request(disconnected: asyncio.Event) -> Request:
    async def receive():
        await disconnected.wait()
        return {"type": "http.disconnect"}

    return Request(
        {"type": "http", "method": "POST", "path": "/fixture", "headers": []},
        receive=receive,
    )


@pytest.mark.asyncio
async def test_already_disconnected_never_starts_operation():
    disconnected = asyncio.Event()
    disconnected.set()
    calls = []

    async def operation():
        calls.append("unexpected")

    with pytest.raises(AppDomainError) as result:
        await run_preview_request(request(disconnected), operation)
    assert result.value.status_code == 499
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_cancel", [False, True])
async def test_disconnect_and_parent_cancel_clean_cooperative_operation(parent_cancel):
    disconnected, started, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
    effects = []

    async def operation():
        effects.append("once")
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    task = asyncio.create_task(run_preview_request(request(disconnected), operation))
    await asyncio.wait_for(started.wait(), 1)
    if parent_cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        disconnected.set()
        with pytest.raises(AppDomainError, match="部分操作可能已完成"):
            await task
    assert cleaned.is_set()
    assert effects == ["once"]
    assert not _retained


@pytest.mark.asyncio
async def test_noncooperative_cleanup_is_bounded_and_retained_until_finished():
    disconnected, started, release, cleaned = (asyncio.Event() for _ in range(4))

    async def operation():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
        finally:
            cleaned.set()

    task = asyncio.create_task(
        run_preview_request(request(disconnected), operation, cleanup_timeout=0.01)
    )
    await asyncio.wait_for(started.wait(), 1)
    disconnected.set()
    with pytest.raises(AppDomainError):
        await asyncio.wait_for(task, 0.5)
    assert not cleaned.is_set()
    assert len(_retained) == 1
    release.set()
    await asyncio.wait_for(cleaned.wait(), 0.5)
    await asyncio.sleep(0)
    assert not _retained


@pytest.mark.asyncio
async def test_disconnect_receive_with_checkpoint_is_not_polled_away():
    """模拟 BaseHTTPMiddleware：接收先让出调度，再交付已就绪的断开。"""
    disconnected, started, cleaned = (asyncio.Event() for _ in range(3))

    async def receive():
        await asyncio.sleep(0)
        await disconnected.wait()
        return {"type": "http.disconnect"}

    incoming = Request(
        {"type": "http", "method": "POST", "path": "/fixture", "headers": []},
        receive=receive,
    )

    async def operation():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    task = asyncio.create_task(run_preview_request(incoming, operation))
    await asyncio.wait_for(started.wait(), 1)
    disconnected.set()
    with pytest.raises(AppDomainError):
        await asyncio.wait_for(task, 1)
    assert cleaned.is_set()
    assert not _retained
