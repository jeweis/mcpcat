"""真实服务重启前后，订阅能力随新代际重新装配。"""

import asyncio

import pytest
from fastapi import FastAPI
from fastmcp import Client
from mcp.client.subscriptions import listen
from mcp.server.mcpserver import MCPServer
from mcp.shared.subscriptions import ToolsListChanged
from test_gateway_client_capabilities import live_http
from test_proxy_subscriptions import CountingBus, ListRecorder, wait_for
from test_storage import database

from app.services.config_service import ConfigService
from app.services.server_manager import MCPServerManager


async def verify_subscription(client, bus, recorder, mode, manager, restart):
    if mode == "legacy":
        await client.list_tools()
        await wait_for(lambda: bus.active == 1)
        await bus.publish(ToolsListChanged())
        await wait_for(lambda: "notifications/tools/list_changed" in recorder.methods)
        if restart:
            assert await manager.restart_server("source")
            await wait_for(lambda: bus.active == 0)
    else:
        async with listen(client.session, tools_list_changed=True) as events:
            assert bus.active == 1
            await bus.publish(ToolsListChanged())
            assert isinstance(await anext(events), ToolsListChanged)
            if restart:
                assert await manager.restart_server("source")
                await wait_for(lambda: bus.active == 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_real_restart_reinstalls_subscription_adapter(
    database, monkeypatch, mode
):
    monkeypatch.setattr(ConfigService, "_database", database)
    bus = CountingBus()
    source = MCPServer("restart fixture", subscriptions=bus)
    async with asyncio.timeout(40), live_http(source.streamable_http_app()) as upstream:
        manager = MCPServerManager()
        config = {"type": "streamable-http", "url": upstream, "require_auth": False}
        assert ConfigService.add_server_to_config("source", config)
        assert manager.add_mcp_server("source", config)
        app = FastAPI(lifespan=manager.create_unified_lifespan)
        manager.mount_all_servers(app)
        async with live_http(app) as url:
            first = manager.server_info["source"]["mcp"]
            for restart in (True, False):
                recorder = ListRecorder()
                async with Client(
                    url + "/source/", mode=mode, message_handler=recorder
                ) as client:
                    await verify_subscription(
                        client, bus, recorder, mode, manager, restart
                    )
                await wait_for(lambda: bus.active == 0)
            assert manager.server_info["source"]["mcp"] is not first
        assert not manager.lifecycle.background
        assert bus.active == 0
