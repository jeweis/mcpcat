"""Catalog 对外目录变化：附带 Skills 与平台 Apps、新旧客户端和身份隔离。"""

import asyncio

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.client.subscriptions import listen
from mcp.shared.subscriptions import ResourcesListChanged, ToolsListChanged
from test_apps_mcp_gateway import uploaded
from test_gateway_client_capabilities import live_http
from test_proxy_subscriptions import ListRecorder, wait_for
from test_skill_content_workflow import READ, content_app
from test_skills_source_gateway import publish_bound, source_app

from app.core.config import settings
from app.services.config_service import ConfigService
from app.services.skill_variant_publication import withdraw_remote_variant
from app.storage.unit_of_work import UnitOfWork


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_catalog_skill_and_app_publication_notifications(
    content_app, monkeypatch, tmp_path, mode
):
    _, database = content_app
    monkeypatch.setattr(settings, "mcpcat_apps_artifacts_path", str(tmp_path / "apps"))
    app = source_app(database, monkeypatch, catalog=True)
    async with asyncio.timeout(40), live_http(app) as url:
        transport = StreamableHttpTransport(
            url + "/mcpcat/", headers={"Mcpcat-Key": READ}
        )
        recorder = ListRecorder()
        async with httpx2.AsyncClient(base_url=url.removesuffix("/mcp")) as admin:
            async with Client(transport, mode=mode, message_handler=recorder) as client:
                assert await client.list_resources() == []
                if mode == "auto":
                    async with listen(
                        client.session,
                        resources_list_changed=True,
                        tools_list_changed=True,
                    ) as events:
                        binding = publish_bound(
                            database,
                            kind="catalog",
                            source="",
                            required=["search_tools", "call_tool"],
                        )
                        assert isinstance(
                            await asyncio.wait_for(anext(events), 5),
                            ResourcesListChanged,
                        )
                        await uploaded(admin)
                        pair = [
                            await asyncio.wait_for(anext(events), 5) for _ in range(2)
                        ]
                        assert {type(event) for event in pair} == {
                            ResourcesListChanged,
                            ToolsListChanged,
                        }
                        resources = await client.list_resources()
                        assert any(
                            str(item.uri).startswith("skill://") for item in resources
                        )
                        assert any(
                            str(item.uri).startswith("ui://") for item in resources
                        )
                        withdraw_remote_variant(
                            database, slug="demo", version="1.0.0", binding=binding
                        )
                        assert isinstance(
                            await asyncio.wait_for(anext(events), 5),
                            ResourcesListChanged,
                        )
                else:
                    publish_bound(
                        database,
                        kind="catalog",
                        source="",
                        required=["search_tools", "call_tool"],
                    )
                    await wait_for(
                        lambda: recorder.methods.count(
                            "notifications/resources/list_changed"
                        )
                        == 1
                    )
                    await uploaded(admin)
                    await wait_for(
                        lambda: recorder.methods.count(
                            "notifications/resources/list_changed"
                        )
                        == 2
                    )
                    await wait_for(
                        lambda: "notifications/tools/list_changed" in recorder.methods
                    )
                    assert await client.list_resources()
            subscriptions = app.state.server_manager.catalog_service.subscriptions
            await wait_for(
                lambda: subscriptions.active == 0 and not subscriptions.legacy.entries
            )


@pytest.mark.asyncio
async def test_public_catalog_does_not_notify_private_skill_publication(
    content_app, monkeypatch
):
    _, database = content_app
    app = source_app(database, monkeypatch, catalog=True)
    config = ConfigService.get_setting_section("catalog", {})
    ConfigService.update_setting_section("catalog", {**config, "require_auth": False})
    async with asyncio.timeout(30), live_http(app) as url:
        async with Client(url + "/mcpcat/") as client:
            async with listen(client.session, resources_list_changed=True) as events:
                pending = asyncio.create_task(anext(events))
                try:
                    publish_bound(
                        database,
                        kind="catalog",
                        source="",
                        required=["search_tools", "call_tool"],
                    )
                    await asyncio.sleep(0.2)
                    assert not pending.done()
                    assert await client.list_resources() == []
                finally:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("ending", ["disable", "revoke"])
async def test_catalog_subscription_lifecycle(content_app, monkeypatch, mode, ending):
    _, database = content_app
    app = source_app(database, monkeypatch, catalog=True)
    subscriptions = app.state.server_manager.catalog_service.subscriptions

    def end():
        if ending == "disable":
            config = ConfigService.get_setting_section("catalog", {})
            assert ConfigService.update_setting_section(
                "catalog", {**config, "enabled": False}
            )
        else:
            with UnitOfWork(database) as unit:
                unit.api_keys.update(READ, {"enabled": False})
                unit.commit()

    async with asyncio.timeout(30), live_http(app) as url:
        transport = StreamableHttpTransport(
            url + "/mcpcat/", headers={"Mcpcat-Key": READ}
        )
        async with Client(transport, mode=mode) as client:
            if mode == "legacy":
                await client.list_resources()
                assert len(subscriptions.legacy.entries) == 1
                end()
                await wait_for(lambda: not subscriptions.legacy.entries)
            else:
                async with listen(client.session, resources_list_changed=True):
                    assert subscriptions.active == 1
                    end()
                    await wait_for(lambda: subscriptions.active == 0)
