"""真实预览调用需要明确确认，仍服从发布/绑定/当前身份。"""

import asyncio
import json

import httpx2
import pytest
from test_apps_mcp_gateway import bound_source, gateway
from test_skill_content_workflow import READ, WRITE, content_app

from app.storage.unit_of_work import UnitOfWork


async def disconnected_request(app, name, started, cleaned):
    """真实 ASGI 中间件链收到 disconnect，而非取消外层应用任务。"""
    messages, responses = asyncio.Queue(), []
    payload = json.dumps(
        {"name": name, "arguments": {"value": 7}, "confirmed": True}
    ).encode()
    await messages.put({"type": "http.request", "body": payload, "more_body": False})

    async def send(message):
        responses.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "server": ("fixture", 80),
        "client": ("127.0.0.1", 1),
        "query_string": b"",
        "root_path": "",
        "path": "/api/apps/bound/versions/1.0.0/preview-call",
        "headers": [
            (b"content-type", b"application/json"),
            (b"mcpcat-key", READ.encode()),
        ],
    }
    task = asyncio.create_task(app(scope, messages.get, send))
    try:
        await asyncio.wait_for(started.wait(), 3)
        await messages.put({"type": "http.disconnect"})
        await asyncio.wait_for(cleaned.wait(), 3)
        await asyncio.wait_for(task, 3)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_http_disconnect_through_auth_middleware_cleans_tool(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path)
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def value(value: int) -> int:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            name = await bound_source(app, client, value)
            await disconnected_request(app, name, started, cleaned)


@pytest.mark.asyncio
async def test_http_parent_cancel_reaches_bound_mcp_tool(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path)
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def value(value: int) -> int:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            name = await bound_source(app, client, value)
            task = asyncio.create_task(
                client.post(
                    "/api/apps/bound/versions/1.0.0/preview-call",
                    headers={"Mcpcat-Key": READ},
                    json={"name": name, "arguments": {"value": 7}, "confirmed": True},
                )
            )
            await asyncio.wait_for(started.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(cleaned.wait(), 3)


@pytest.mark.asyncio
async def test_preview_call_confirmed_once_and_scope_checked(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path)
    calls = []

    def value(value: int) -> dict:
        calls.append(value)
        return {"saved": value}

    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            name = await bound_source(app, client, value)
            path = "/api/apps/bound/versions/1.0.0/preview-call"
            body = {"name": name, "arguments": {"value": 7}}
            assert (
                await client.post(path, headers={"Mcpcat-Key": READ}, json=body)
            ).status_code == 400
            body["confirmed"] = "yes"
            assert (
                await client.post(path, headers={"Mcpcat-Key": READ}, json=body)
            ).status_code == 422
            body["confirmed"] = True
            wrong = await client.post(
                path, headers={"Mcpcat-Key": READ}, json={**body, "name": "value"}
            )
            assert wrong.status_code == 403
            assert calls == []
            response = await client.post(path, headers={"Mcpcat-Key": READ}, json=body)
            assert response.status_code == 200, response.text
            assert response.json()["structuredContent"] == {"saved": 7}
            assert calls == [7]
            assert response.headers["cache-control"] == "private, no-store"
            assert WRITE not in response.text and READ not in response.text
            app.state.server_manager.server_info["math"]["status"] = "stopped"
            assert (
                await client.post(path, headers={"Mcpcat-Key": READ}, json=body)
            ).status_code == 409
            assert calls == [7]


@pytest.mark.asyncio
async def test_preview_call_revocation_after_effect_is_not_retried(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path)
    calls = []

    def revoke(value: int) -> int:
        calls.append(value)
        with UnitOfWork(database) as unit:
            unit.api_keys.update(READ, {"enabled": False})
            unit.commit()
        return value + 1

    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            name = await bound_source(app, client, revoke)
            response = await client.post(
                "/api/apps/bound/versions/1.0.0/preview-call",
                headers={"Mcpcat-Key": READ},
                json={"name": name, "arguments": {"value": 7}, "confirmed": True},
            )
            assert response.status_code in {403, 409}, response.text
            assert "部分操作可能已完成" in response.json()["detail"]
            assert calls == [7]


@pytest.mark.asyncio
async def test_preview_call_bounds_arguments_and_rejects_withdrawn(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path)
    calls = []

    def value(value: int) -> int:
        calls.append(value)
        return value

    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            name = await bound_source(app, client, value)
            path = "/api/apps/bound/versions/1.0.0/preview-call"
            body = {
                "name": name,
                "confirmed": True,
                "arguments": {"value": "x" * (512 * 1024)},
            }
            assert (
                await client.post(path, headers={"Mcpcat-Key": READ}, json=body)
            ).status_code == 413
            await client.post(
                "/api/apps/bound/versions/1.0.0/withdraw", headers={"Mcpcat-Key": WRITE}
            )
            body["arguments"] = {"value": 7}
            assert (
                await client.post(path, headers={"Mcpcat-Key": WRITE}, json=body)
            ).status_code == 404
            assert calls == []
