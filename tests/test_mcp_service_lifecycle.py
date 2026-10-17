"""实际 FastMCP lifespan 的初始/动态服务管理，不启动用户配置的程序。"""

import asyncio
from contextlib import asynccontextmanager

import httpx2
import pytest
from fastapi import FastAPI
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from test_storage import database

from app.services import mcp_lifecycle
from app.services.config_service import ConfigService
from app.services.mcp_factory import MCPServerFactory
from app.services.server_manager import MCPServerManager

CONFIG = {"type": "stdio", "command": "fixture-not-executed", "args": []}


def setup_manager(database, monkeypatch, waiting=None):
    monkeypatch.setattr(ConfigService, "_database", database)
    evidence = []

    def factory(name, config):
        generation = config.get("note", "first")

        @asynccontextmanager
        async def lifespan(server):
            task = asyncio.current_task()
            evidence.append((generation, "enter", task))
            try:
                yield
            finally:
                evidence.append((generation, "exit", asyncio.current_task()))

        server = FastMCP(name, lifespan=lifespan)

        @server.tool()
        def identity() -> str:
            return generation

        if waiting:

            @server.tool()
            async def hold() -> str:
                waiting[0].set()
                try:
                    await asyncio.Event().wait()
                finally:
                    waiting[1].set()
                return "unreachable"

        return server

    monkeypatch.setattr(MCPServerFactory, "create_server", factory)
    assert ConfigService.add_server_to_config("fixture", CONFIG)
    manager = MCPServerManager()
    manager.load_servers_from_config()
    app = FastAPI(lifespan=manager.create_unified_lifespan)
    manager.mount_all_servers(app)
    return manager, app, evidence


def client_for(app, mode):
    transport = StreamableHttpTransport(
        "http://fixture/mcp/fixture/",
        httpx_client_factory=lambda **kwargs: httpx2.AsyncClient(
            **kwargs, transport=httpx2.ASGITransport(app)
        ),
    )
    return Client(transport, mode=mode)


def assert_clean(manager):
    assert not manager.app_started and manager.main_app is None
    assert not manager.lifecycle.handles and not manager.dynamic_tasks
    assert not manager.lifecycle.background and not manager.lifecycle.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_initial_service_really_stops_restarts_and_cleans(
    database, monkeypatch, mode
):
    manager, app, evidence = setup_manager(database, monkeypatch)
    async with app.router.lifespan_context(app):
        async with client_for(app, mode) as client:
            assert (await client.call_tool("identity", {})).data == "first"
        assert await manager.stop_server("fixture")
        assert [step[1] for step in evidence] == ["enter", "exit"]
        assert evidence[0][2] is evidence[1][2]
        assert await manager.start_server("fixture")
        assert await manager.restart_server("fixture", {**CONFIG, "note": "second"})
        async with client_for(app, mode) as client:
            assert (await client.call_tool("identity", {})).data == "second"
    assert [step[1] for step in evidence] == [
        "enter",
        "exit",
        "enter",
        "exit",
        "enter",
        "exit",
    ]
    assert_clean(manager)


@pytest.mark.asyncio
async def test_concurrent_start_enters_only_once_and_remove_readd(
    database, monkeypatch
):
    manager, app, evidence = setup_manager(database, monkeypatch)
    async with app.router.lifespan_context(app):
        assert all(
            await asyncio.gather(*(manager.start_server("fixture") for _ in range(4)))
        )
        assert len([step for step in evidence if step[1] == "enter"]) == 1
        assert await manager.remove_server("fixture")
        assert await manager.add_and_mount_server(
            app, "fixture", {**CONFIG, "note": "new"}
        )
        assert (
            sum(
                getattr(route, "path", "") == "/mcp/fixture"
                for route in app.router.routes
            )
            == 1
        )
        assert len(manager.get_mount_list()) == 2
        async with client_for(app, "auto") as client:
            assert (await client.call_tool("identity", {})).data == "new"
    assert (
        len([step for step in evidence if step[1] == "enter"])
        == len([step for step in evidence if step[1] == "exit"])
        == 2
    )
    assert_clean(manager)


@pytest.mark.asyncio
async def test_invalid_restart_keeps_previous_generation_running(database, monkeypatch):
    manager, app, evidence = setup_manager(database, monkeypatch)
    async with app.router.lifespan_context(app):
        assert not await manager.restart_server("fixture", {"type": "unknown"})
        assert manager.server_info["fixture"]["status"] == "running"
        assert len(evidence) == 1
        async with client_for(app, "auto") as client:
            assert (await client.call_tool("identity", {})).data == "first"
    assert_clean(manager)


@pytest.mark.asyncio
async def test_startup_failure_recoverable_without_double_click(database, monkeypatch):
    manager, app, evidence = setup_manager(database, monkeypatch)
    original = manager.lifespan_tasks["fixture"]

    @asynccontextmanager
    async def fail(app):
        raise RuntimeError("fixture-secret-from-lifespan")
        yield

    manager.lifespan_tasks["fixture"] = fail
    async with app.router.lifespan_context(app):
        assert manager.server_info["fixture"]["status"] == "failed"
        manager.lifespan_tasks["fixture"] = original
        assert await manager.start_server("fixture")
        async with client_for(app, "auto") as client:
            assert (await client.call_tool("identity", {})).data == "first"
    assert_clean(manager)


@pytest.mark.asyncio
async def test_startup_timeout_cleans_partial_context(database, monkeypatch):
    manager, app, evidence = setup_manager(database, monkeypatch)
    cleaned = asyncio.Event()

    @asynccontextmanager
    async def slow(app):
        try:
            await asyncio.Event().wait()
            yield
        finally:
            cleaned.set()

    manager.lifespan_tasks["fixture"] = slow
    monkeypatch.setattr(mcp_lifecycle, "START_TIMEOUT", 0.02)
    async with app.router.lifespan_context(app):
        assert manager.server_info["fixture"]["status"] == "failed"
        assert cleaned.is_set()
        assert not manager.dynamic_tasks
    assert_clean(manager)


@pytest.mark.asyncio
async def test_slow_cleanup_not_reported_stopped_or_restarted(database, monkeypatch):
    manager, app, evidence = setup_manager(database, monkeypatch)
    release, closing = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def delayed_exit(app):
        try:
            yield
        finally:
            closing.set()
            await release.wait()

    manager.lifespan_tasks["fixture"] = delayed_exit
    monkeypatch.setattr(mcp_lifecycle, "STOP_TIMEOUT", 0.02)
    async with app.router.lifespan_context(app):
        assert not await manager.stop_server("fixture")
        assert closing.is_set()
        assert manager.server_info["fixture"]["status"] == "failed"
        task = manager.lifecycle.handles["fixture"].task
        assert not task.done() and not await manager.start_server("fixture")
        release.set()
        await asyncio.wait({task}, timeout=2)
        assert task.done()
        assert await manager.stop_server("fixture")
    assert_clean(manager)


@pytest.mark.asyncio
async def test_remove_save_failure_keeps_stopped_record_for_retry(
    database, monkeypatch
):
    manager, app, evidence = setup_manager(database, monkeypatch)
    original = ConfigService.remove_server_from_config
    async with app.router.lifespan_context(app):
        monkeypatch.setattr(
            ConfigService, "remove_server_from_config", lambda name: False
        )
        assert not await manager.remove_server("fixture")
        assert manager.server_info["fixture"]["status"] == "stopped"
        assert ConfigService.get_server_config("fixture") is not None
        monkeypatch.setattr(ConfigService, "remove_server_from_config", original)
        assert await manager.remove_server("fixture")
        assert "fixture" not in manager.server_info
    assert_clean(manager)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_stop_cancels_actual_mcp_tool_and_its_http_request(
    database, monkeypatch, mode
):
    waiting = asyncio.Event(), asyncio.Event()
    manager, app, evidence = setup_manager(database, monkeypatch, waiting)
    async with app.router.lifespan_context(app), client_for(app, mode) as client:
        task = asyncio.create_task(client.call_tool("hold", {}))
        await asyncio.wait_for(waiting[0].wait(), 2)
        assert await manager.stop_server("fixture")
        assert waiting[1].is_set()
        done, pending = await asyncio.wait({task}, timeout=2)
        if pending:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert done
        assert task.cancelled() or task.exception() is not None
        assert not manager.lifecycle.requests
    assert_clean(manager)


@pytest.mark.asyncio
async def test_background_auth_cancelled_before_shutdown_returns(database, monkeypatch):
    manager, app, evidence = setup_manager(database, monkeypatch)
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def blocked_auth(name, config):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    monkeypatch.setattr(manager, "_detect_auth_status", blocked_auth)
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(entered.wait(), 2)
    assert cleaned.is_set()
    assert_clean(manager)
