"""旧客户端经真实 HTTP 收取平台目录变化，并释放会话监听。"""

import asyncio

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from test_apps_mcp_gateway import gateway, uploaded
from test_gateway_client_capabilities import live_http
from test_proxy_subscriptions import ListRecorder, ResourceRecorder, wait_for
from test_remote_skill_protocol import skill_package
from test_skill_content_workflow import READ, content_app
from test_skills_mcp_gateway import configured_app

from app.services.skill_profile_upload import upload_skill_profiles
from app.services.skill_variant_publication import publish_content_version
from app.storage.unit_of_work import UnitOfWork
from app.services.skill_resource_uri import SkillResourceIdentity, skill_resource_uri


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["skills", "apps"])
@pytest.mark.parametrize("ending", ["disconnect", "revoke", "stop"])
async def test_legacy_registry_lists_and_session_cleanup(
    content_app, monkeypatch, tmp_path, kind, ending
):
    _, database = content_app
    app = (
        configured_app(database, monkeypatch)
        if kind == "skills"
        else gateway(database, monkeypatch, tmp_path)
    )
    runtime = getattr(app.state, f"{kind}_mcp_runtime")
    async with asyncio.timeout(30), live_http(app) as url:
        transport = StreamableHttpTransport(
            url + f"/{kind}/", headers={"Mcpcat-Key": READ}
        )
        recorder = ListRecorder()
        async with Client(transport, mode="legacy", message_handler=recorder) as client:
            assert client.server_capabilities.resources.subscribe is True
            assert client.server_capabilities.resources.list_changed is True
            assert await client.list_resources() == []
            assert await client.list_resources() == []
            assert len(runtime.subscriptions.legacy.entries) == 1
            if kind == "skills":
                upload_skill_profiles(
                    database,
                    content=skill_package(),
                    version="1.0.0",
                    selection="remote",
                )
                publish_content_version(database, slug="demo", version="1.0.0")
            else:
                assert client.server_capabilities.tools.list_changed is True
                async with httpx2.AsyncClient(
                    base_url=url.removesuffix("/mcp")
                ) as admin:
                    await uploaded(admin)
            await wait_for(
                lambda: "notifications/resources/list_changed" in recorder.methods
            )
            if kind == "apps":
                await wait_for(
                    lambda: "notifications/tools/list_changed" in recorder.methods
                )
            uri = str((await client.list_resources())[0].uri)
            await client.session.subscribe_resource(uri)
            await client.session.subscribe_resource(uri)
            entry = next(iter(runtime.subscriptions.legacy.entries.values()))
            assert entry.uris == {uri}
            await client.session.unsubscribe_resource(uri)
            assert entry.uris == set()
            if ending == "revoke":
                with UnitOfWork(database) as unit:
                    unit.api_keys.update(READ, {"enabled": False})
                    unit.commit()
            elif ending == "stop":
                runtime.alive = False
            if ending != "disconnect":
                await wait_for(lambda: not runtime.subscriptions.legacy.entries)
        await wait_for(lambda: not runtime.subscriptions.legacy.entries)


@pytest.mark.asyncio
async def test_legacy_resource_notification_only_after_visible_publication(
    content_app, monkeypatch
):
    _, database = content_app
    app = configured_app(database, monkeypatch)
    runtime = app.state.skills_mcp_runtime
    upload_skill_profiles(
        database, content=skill_package(), version="1.0.0", selection="remote"
    )
    with UnitOfWork(database) as unit:
        skill_id = unit.skills.get_by_slug("demo").id
    uri = skill_resource_uri(
        SkillResourceIdentity(
            runtime.scope.instance, runtime.scope.endpoint, skill_id, "1.0.0", "demo"
        )
    )
    recorder = ResourceRecorder()
    async with asyncio.timeout(20), live_http(app) as url:
        transport = StreamableHttpTransport(
            url + "/skills/", headers={"Mcpcat-Key": READ}
        )
        async with Client(transport, mode="legacy", message_handler=recorder) as client:
            await client.session.subscribe_resource(uri)
            await asyncio.sleep(0.1)
            assert recorder.uris == []
            publish_content_version(database, slug="demo", version="1.0.0")
            await wait_for(lambda: recorder.uris == [uri])
            assert await client.read_resource(uri)
            await client.session.unsubscribe_resource(uri)
            await wait_for(lambda: not runtime.subscriptions.legacy.entries)
