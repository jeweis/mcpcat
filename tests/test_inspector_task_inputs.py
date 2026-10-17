"""任务输入适配的容量与整轮预校验，所有数据为合成夹具。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp import types

from app.services.inspector_task_inputs import (
    answer_task_inputs, interruptible_answers, resolve_task_inputs,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("requests", [
    {}, {str(i): {"method": "roots/list"} for i in range(17)},
    {"first": {"method": "roots/list"}, "second": {"method": "unknown"}},
    {"first": {"method": "roots/list", "padding": "中" * 65536}},
])
async def test_invalid_batch_never_dispatches_or_sends(requests: dict) -> None:
    """整轮校验失败不能先触发第一个问题的回调或发送更新。"""
    session = SimpleNamespace(dispatch_input_request=AsyncMock(), send_request=AsyncMock())
    with pytest.raises(ValueError):
        await answer_task_inputs(SimpleNamespace(session=session), "fixture", requests)
    session.dispatch_input_request.assert_not_awaited()
    session.send_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_ack_does_not_invent_remote_cancelled_state() -> None:
    """来源接受取消请求但仍完成时，返回实际结果，不虚报已取消。"""
    cancelled = asyncio.Event()
    cancelled.set()
    handle = SimpleNamespace(
        status=AsyncMock(side_effect=[
            SimpleNamespace(status="input_required", poll_interval_ms=1),
            SimpleNamespace(status="working", poll_interval_ms=1),
            SimpleNamespace(status="completed"),
        ]), result=AsyncMock(return_value="actual-result"),
    )
    session = SimpleNamespace(dispatch_input_request=AsyncMock(), send_request=AsyncMock())
    result = await resolve_task_inputs(SimpleNamespace(session=session), handle, cancelled)
    assert result == "actual-result"
    session.dispatch_input_request.assert_not_awaited()
    session.send_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_closing_answer_driver_releases_callback() -> None:
    """关闭会话的取消会传入回调，释放等待而不发送迟到回答。"""
    entered, released = asyncio.Event(), asyncio.Event()

    async def waiting(context, request):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            released.set()

    session = SimpleNamespace(dispatch_input_request=waiting, send_request=AsyncMock())
    worker = asyncio.create_task(interruptible_answers(
        SimpleNamespace(session=session), "fixture", {"roots": {"method": "roots/list"}},
        asyncio.Event(),
    ))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        assert released.is_set()
        session.send_request.assert_not_awaited()
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_oversize_answer_not_sent() -> None:
    """SDK 回调结果同样受容量限制，不向上游发送超限内容。"""
    session = SimpleNamespace(
        dispatch_input_request=AsyncMock(return_value=types.ListRootsResult(roots=[
            types.Root(uri="file:///fixture", name="中" * 65536),
        ])), send_request=AsyncMock(),
    )
    with pytest.raises(ValueError, match="回答超过"):
        await answer_task_inputs(SimpleNamespace(session=session), "fixture", {
            "roots": {"method": "roots/list"},
        })
    session.send_request.assert_not_awaited()
