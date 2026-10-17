"""现代 JSON 断连的有界收尾；旧协议继续使用 SDK 原生取消。"""

import asyncio
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.services import app_mcp_cancellation
from app.services.app_mcp_cancellation import AppDisconnectMiddleware
from app.services.app_preview_cancellation import _retained


def request(disconnected, protocol):
    async def receive():
        await disconnected.wait()
        return {"type": "http.disconnect"}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/mcp/apps",
            "headers": [(b"mcp-protocol-version", protocol.encode())],
        },
        receive=receive,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["2025-11-25", "2026-07-28"])
async def test_live_call_preserves_result_and_releases_listener(monkeypatch, protocol):
    incoming = request(asyncio.Event(), protocol)
    monkeypatch.setattr(app_mcp_cancellation, "get_http_request", lambda: incoming)
    result = object()

    async def call_next(context):
        return result

    middleware = AppDisconnectMiddleware()
    assert await middleware.on_call_tool(SimpleNamespace(), call_next) is result
    assert not _retained


@pytest.mark.asyncio
async def test_modern_disconnect_retains_uncooperative_cleanup_until_finished(
    monkeypatch,
):
    disconnected, entered, release, cleaned = (asyncio.Event() for _ in range(4))
    incoming = request(disconnected, "2026-07-28")
    monkeypatch.setattr(app_mcp_cancellation, "get_http_request", lambda: incoming)

    async def call_next(context):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
        finally:
            cleaned.set()

    pending = asyncio.create_task(
        AppDisconnectMiddleware().on_call_tool(SimpleNamespace(), call_next)
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        disconnected.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 2)
        assert not cleaned.is_set() and _retained
    finally:
        release.set()
        await asyncio.wait_for(cleaned.wait(), 1)
        await asyncio.sleep(0)
    assert not _retained
