"""代理 ASGI 边界：不缓冲/二次发送响应，不吞取消，不泄露异常正文。"""

import asyncio

import pytest

from app.services.server_manager import MCPProxyApp, MCPServerManager

SCOPE = {
    "type": "http",
    "method": "POST",
    "path": "/",
    "headers": [],
    "asgi": {"version": "3.0"},
}


async def receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def gateway(application):
    manager = MCPServerManager()
    manager.server_info["fixture"] = {"status": "running", "mcp_app": application}
    return manager, MCPProxyApp("fixture", manager)


@pytest.mark.asyncio
async def test_stream_forwarded_immediately_and_one_generation_per_request():
    first, finish = asyncio.Event(), asyncio.Event()
    messages = []

    async def stream(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"x-source", b"old")],
            }
        )
        await send({"type": "http.response.body", "body": b"one", "more_body": True})
        first.set()
        await finish.wait()
        await send({"type": "http.response.body", "body": b"two", "more_body": False})

    async def changed(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"new", "more_body": False})

    manager, proxy = gateway(stream)

    async def send(message):
        messages.append(message)

    task = asyncio.create_task(proxy(SCOPE, receive, send))
    await asyncio.wait_for(first.wait(), 2)
    assert not task.done() and messages[-1]["body"] == b"one"
    manager.server_info["fixture"]["mcp_app"] = changed
    finish.set()
    await task
    assert [item.get("body") for item in messages if "body" in item] == [b"one", b"two"]
    messages.clear()
    await proxy(SCOPE, receive, send)
    assert messages[-1]["body"] == b"new"
    assert not manager.lifecycle.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True])
async def test_error_boundary_never_starts_response_twice_or_leaks_details(
    started, caplog
):
    messages = []

    async def fail(scope, receive, send):
        if started:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send(
                {"type": "http.response.body", "body": b"part", "more_body": True}
            )
        raise RuntimeError("fixture-secret-from-upstream")

    manager, proxy = gateway(fail)

    async def send(message):
        messages.append(message)

    if started:
        with pytest.raises(RuntimeError, match="流式请求已中断"):
            await proxy(SCOPE, receive, send)
        assert messages[-1]["body"] == b"part"
    else:
        await proxy(SCOPE, receive, send)
        assert messages[0]["status"] == 500
    assert sum(item["type"] == "http.response.start" for item in messages) == 1
    assert b"fixture-secret" not in b"".join(item.get("body", b"") for item in messages)
    assert "fixture-secret" not in caplog.text
    assert not manager.lifecycle.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_stop_cancels_request_and_waits_for_its_finally(streaming):
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def waiting(scope, receive, send):
        if streaming:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send(
                {
                    "type": "http.response.body",
                    "body": b"data: open\n\n",
                    "more_body": True,
                }
            )
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    manager, proxy = gateway(waiting)
    messages = []

    async def send(message):
        messages.append(message)

    task = asyncio.create_task(proxy(SCOPE, receive, send))
    await asyncio.wait_for(entered.wait(), 2)
    assert await manager.stop_server("fixture")
    await task
    starts = [
        message for message in messages if message["type"] == "http.response.start"
    ]
    assert len(starts) == 1
    assert starts[0]["status"] == (200 if streaming else 503)
    assert messages[-1]["type"] == "http.response.body"
    assert not messages[-1].get("more_body", False)
    assert cleaned.is_set() and not manager.lifecycle.requests
    assert not manager.lifecycle.stopping_requests
    await manager.lifecycle.close()


@pytest.mark.asyncio
async def test_client_cancellation_propagates_without_a_response():
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def waiting(scope, receive, send):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    manager, proxy = gateway(waiting)

    async def send(message):
        raise AssertionError("Client cancellation must not generate a response")

    task = asyncio.create_task(proxy(SCOPE, receive, send))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set() and not manager.lifecycle.requests
    assert not manager.lifecycle.stopping_requests
    await manager.lifecycle.close()


@pytest.mark.asyncio
async def test_websocket_error_is_a_close_not_an_http_message():
    manager = MCPServerManager()
    proxy = MCPProxyApp("missing", manager)
    messages = []

    async def send(message):
        messages.append(message)

    await proxy({"type": "websocket"}, receive, send)
    assert messages == [{"type": "websocket.close", "code": 1008}]
