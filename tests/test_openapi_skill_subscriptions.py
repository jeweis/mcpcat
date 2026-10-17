"""真实 OpenAPIProvider 来源的 Skill 发布通知与停止/重启清理。"""

import asyncio
from types import SimpleNamespace

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.client.subscriptions import listen
from mcp.shared.subscriptions import ResourcesListChanged
from test_gateway_client_capabilities import live_http
from test_proxy_subscriptions import ListRecorder, wait_for
from test_remote_skill_protocol import skill_package
from test_skill_content_workflow import READ, content_app

from app.application import create_app
from app.services.config_service import ConfigService
from app.services.inspector_service import inspector_service
from app.services.market_service import MarketService
from app.services.server_manager import MCPServerManager
from app.services.skill_profile_upload import upload_skill_profiles
from app.services.skill_variant_publication import publish_content_version
from app.storage.runtime_identities import endpoint_identity


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_openapi_skill_notifications_survive_restart(
    content_app, monkeypatch, mode
):
    _, database = content_app
    specification = {
        "openapi": "3.1.0",
        "info": {"title": "fixture", "version": "1.0"},
        "paths": {
            "/echo": {
                "get": {
                    "operationId": "echo",
                    "responses": {
                        "200": {
                            "description": "fixture",
                            "content": {
                                "application/json": {"schema": {"type": "string"}}
                            },
                        }
                    },
                }
            }
        },
    }
    fetched = []

    def fetch(url):
        assert url == "http://fixture.invalid/openapi.json"
        fetched.append(url)
        return SimpleNamespace(json=lambda: specification)

    monkeypatch.setattr(httpx2, "get", fetch)
    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    ConfigService.update_setting_section("catalog", {"enabled": False})
    assert ConfigService.add_server_to_config(
        "alpha",
        {
            "type": "openapi",
            "spec_url": "http://fixture.invalid/openapi.json",
            "api_base_url": "http://fixture.invalid",
            "require_auth": True,
            "route_configs": [{"methods": ["GET"], "pattern": ".*"}],
        },
    )
    app = create_app(database=database, server_manager=MCPServerManager())
    manager = app.state.server_manager
    binding = endpoint_identity(database, "service", "alpha")
    async with asyncio.timeout(40), live_http(app) as url:
        for index in range(2):
            if index:
                assert await manager.restart_server("alpha")
            attachment = app.state.skills_mcp_runtime.attachments[binding]
            assert attachment.subscriptions is not None
            recorder = ListRecorder()
            transport = StreamableHttpTransport(
                url + "/alpha/", headers={"Mcpcat-Key": READ}
            )
            async with Client(transport, mode=mode, message_handler=recorder) as client:
                tools = await client.list_tools()
                assert len(tools) == 1

                def publish():
                    version = f"1.0.{index}"
                    upload_skill_profiles(
                        database,
                        content=skill_package(),
                        version=version,
                        selection="remote",
                        binding=binding,
                        metadata={
                            "binding_kind": "service",
                            "source_name": "alpha",
                            "required_tools": [tools[0].name],
                        },
                    )
                    publish_content_version(database, slug="demo", version=version)

                if mode == "legacy":
                    assert client.server_capabilities.resources.subscribe
                    await client.list_resources()
                    publish()
                    await wait_for(
                        lambda: "notifications/resources/list_changed"
                        in recorder.methods
                    )
                    assert len(attachment.subscriptions.legacy.entries) == 1
                    if index:
                        assert await manager.stop_server("alpha")
                        assert not attachment.subscriptions.legacy.entries
                else:
                    async with listen(
                        client.session, resources_list_changed=True
                    ) as events:
                        publish()
                        assert isinstance(
                            await asyncio.wait_for(anext(events), 5),
                            ResourcesListChanged,
                        )
            await wait_for(
                lambda: attachment.subscriptions.active == 0
                and not attachment.subscriptions.legacy.entries
            )
        assert len(fetched) == 2
