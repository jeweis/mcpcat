"""真实 TCP Catalog/App 代理的取消清理；不用内存 HTTP 模拟断连。"""

import asyncio
import base64
from contextlib import AsyncExitStack

import httpx2
import pytest
from fastmcp.apps import UI_MIME_TYPE, AppConfig
from test_apps_mcp_gateway import bound_source
from test_catalog_upstream_apps import relay_config
from test_code_mode_upstream_auth import ProtectedSource, connection
from test_skill_content_workflow import READ
from test_skill_content_workflow import content_app as _content_app

from app.application import create_app
from app.core.config import settings
from app.services.config_service import ConfigService
from app.services.inspector_service import inspector_service
from app.services.market_service import MarketService
from app.services.server_manager import MCPServerManager
from scripts.probe_fastmcp_network import serve_app

content_app = _content_app
UI = "ui://same/slow.html"


async def gateway(stack, database, monkeypatch, tmp_path):
    """固定无凭证业务夹具，使用真实代理工厂及临时监听端口。"""
    source = ProtectedSource("alpha")

    @source.server.tool(app=AppConfig(resourceUri=UI))
    def open_ui() -> str:
        return "Fixture App; interactive rendering requires an Apps Host."

    @source.server.resource(UI, mime_type=UI_MIME_TYPE)
    def ui() -> str:
        return "<!doctype html><p>Trusted cancellation fixture</p>"

    source.resource_entered, source.resource_cleaned = asyncio.Event(), asyncio.Event()

    @source.server.resource("data://slow", mime_type="application/octet-stream")
    async def slow_resource() -> bytes:
        source.resource_entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            source.resource_cleaned.set()
        return b"never"

    root = await stack.enter_async_context(serve_app(source))
    ConfigService.add_server_to_config(
        "alpha",
        {
            "type": "streamable-http",
            "url": root + "/",
            "expose_in_catalog": True,
            "headers": {"Authorization": f"Bearer {source.token}"},
        },
    )
    monkeypatch.setattr(settings, "mcpcat_apps_artifacts_path", str(tmp_path / "apps"))
    ConfigService.update_setting_section("apps", {"enabled": True})
    ConfigService.update_setting_section("catalog", {"enabled": True})
    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    app = create_app(database=database, server_manager=MCPServerManager())
    root = await stack.enter_async_context(serve_app(app))
    return root, app, source


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_catalog_client_cancel_reaches_real_upstream(
    content_app, monkeypatch, tmp_path, mode
):
    _, database = content_app
    async with AsyncExitStack() as stack:
        root, _, source = await gateway(stack, database, monkeypatch, tmp_path)
        async with connection(root, READ, mode, path="mcpcat") as client:
            opening = next(
                tool
                for tool in await client.list_tools()
                if tool.name == "alpha__open_ui"
            )
            resource = await client.read_resource(opening.meta["ui"]["resourceUri"])
            callback = relay_config(resource[0].text)["tools"]["value"]
            pending = asyncio.create_task(client.call_tool(callback, {"value": 500}))
            try:
                await asyncio.wait_for(source.entered.wait(), 5)
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await asyncio.wait_for(source.cleaned.wait(), 5)
            assert source.calls == [500]
            result = await client.call_tool(callback, {"value": 1})
            assert result.data == "alpha:1"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_catalog_resource_cancel_reaches_real_upstream(
    content_app, monkeypatch, tmp_path, mode
):
    _, database = content_app
    async with AsyncExitStack() as stack:
        root, _, source = await gateway(stack, database, monkeypatch, tmp_path)
        async with connection(root, READ, mode, path="mcpcat") as client:
            opening = next(
                tool
                for tool in await client.list_tools()
                if tool.name == "alpha__open_ui"
            )
            token = base64.urlsafe_b64encode(b"data://slow").decode().rstrip("=")
            uri = opening.meta["ui"]["resourceUri"].rsplit("/", 1)[0] + "/" + token
            pending = asyncio.create_task(client.read_resource(uri))
            try:
                await asyncio.wait_for(source.resource_entered.wait(), 5)
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await asyncio.wait_for(source.resource_cleaned.wait(), 5)
            assert await client.list_tools()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("path", ["apps", "mcpcat"])
async def test_uploaded_app_callback_cancel_releases_local_tool(
    content_app, monkeypatch, tmp_path, mode, path
):
    _, database = content_app
    entered, cleaned = asyncio.Event(), asyncio.Event()
    calls = []

    async def value(value: int) -> int:
        calls.append(value)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()
        return value

    async with AsyncExitStack() as stack:
        root, app, _ = await gateway(stack, database, monkeypatch, tmp_path)
        original = dict(app.state.server_manager.server_info)
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as management:
            callback = await bound_source(app, management, value)
        app.state.server_manager.server_info.update(original)
        async with connection(root, READ, mode, path=path) as client:
            pending = asyncio.create_task(client.call_tool(callback, {"value": 7}))
            try:
                await asyncio.wait_for(entered.wait(), 5)
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await asyncio.wait_for(cleaned.wait(), 5)
            assert calls == [7]
            assert await client.list_tools()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_stop_source_cancels_catalog_call_and_cleans_manager(
    content_app, monkeypatch, tmp_path, mode
):
    _, database = content_app
    async with AsyncExitStack() as stack:
        root, app, source = await gateway(stack, database, monkeypatch, tmp_path)
        manager = app.state.server_manager
        async with connection(root, READ, mode, path="mcpcat") as client:
            opening = next(
                tool
                for tool in await client.list_tools()
                if tool.name == "alpha__open_ui"
            )
            resource = await client.read_resource(opening.meta["ui"]["resourceUri"])
            callback = relay_config(resource[0].text)["tools"]["value"]
            pending = asyncio.create_task(
                client.call_tool(callback, {"value": 500}, raise_on_error=False)
            )
            try:
                await asyncio.wait_for(source.entered.wait(), 5)
                assert manager.lifecycle.requests.get("alpha")
                assert await asyncio.wait_for(manager.stop_server("alpha"), 5)
                await asyncio.wait_for(source.cleaned.wait(), 5)
                outcome = (
                    await asyncio.wait_for(
                        asyncio.gather(pending, return_exceptions=True), 5
                    )
                )[0]
                assert isinstance(outcome, BaseException) or outcome.is_error
                assert not manager.lifecycle.requests.get("alpha")
                assert not manager.lifecycle.stopping_requests
                assert source.calls == [500]
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
