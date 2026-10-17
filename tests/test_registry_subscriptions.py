"""真实 Skills 网关发布通知及匿名/认证可见性隔离。"""

import asyncio

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.client.subscriptions import listen
from mcp.shared.exceptions import MCPError
from mcp.shared.subscriptions import ResourcesListChanged
from test_gateway_client_capabilities import live_http
from test_remote_skill_protocol import skill_package
from test_skill_content_workflow import READ, content_app
from test_skills_mcp_gateway import configured_app

from app.services.skill_profile_upload import upload_skill_profiles
from app.services.skill_variant_publication import (
    publish_content_version,
    withdraw_remote_variant,
)
from app.storage.unit_of_work import UnitOfWork


async def wait_until_closed(runtime):
    async with asyncio.timeout(3):
        while runtime.subscriptions.active:
            await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_real_registry_changes_hide_drafts_and_private_versions(
    content_app, monkeypatch
):
    _, database = content_app
    app = configured_app(database, monkeypatch, require_auth=False)
    async with asyncio.timeout(20), live_http(app) as url:
        endpoint = url + "/skills/"
        transport = StreamableHttpTransport(endpoint, headers={"Mcpcat-Key": READ})
        async with Client(endpoint) as anonymous, Client(transport) as reader:
            async with listen(
                anonymous.session, resources_list_changed=True
            ) as public_events:
                async with listen(
                    reader.session, resources_list_changed=True
                ) as reader_events:
                    public_next = asyncio.create_task(anext(public_events))
                    reader_next = asyncio.create_task(anext(reader_events))
                    try:
                        upload_skill_profiles(
                            database,
                            content=skill_package(),
                            version="1.0.0",
                            selection="remote",
                        )
                        await asyncio.sleep(0.1)
                        assert not public_next.done() and not reader_next.done()
                        publish_content_version(
                            database, slug="demo", version="1.0.0", remote_public=False
                        )
                        assert isinstance(
                            await asyncio.wait_for(reader_next, 3), ResourcesListChanged
                        )
                        assert not public_next.done()
                        assert await anonymous.list_resources() == []
                        assert await reader.list_resources()
                        upload_skill_profiles(
                            database,
                            content=skill_package(body=b"# Public"),
                            version="1.1.0",
                            selection="remote",
                        )
                        publish_content_version(
                            database, slug="demo", version="1.1.0", remote_public=True
                        )
                        assert isinstance(
                            await asyncio.wait_for(public_next, 3), ResourcesListChanged
                        )
                        assert await anonymous.list_resources()
                    finally:
                        for task in (public_next, reader_next):
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(
                            public_next, reader_next, return_exceptions=True
                        )
            await wait_until_closed(app.state.skills_mcp_runtime)


@pytest.mark.asyncio
@pytest.mark.parametrize("require_auth", [False, True])
@pytest.mark.parametrize("ending", ["revoke", "stop", "unsubscribe"])
async def test_registry_subscription_release_on_identity_or_lifecycle_end(
    content_app, monkeypatch, require_auth, ending
):
    _, database = content_app
    app = configured_app(database, monkeypatch, require_auth=require_auth)
    runtime = app.state.skills_mcp_runtime
    async with asyncio.timeout(20), live_http(app) as url:
        transport = StreamableHttpTransport(
            url + "/skills/", headers={"Mcpcat-Key": READ}
        )
        async with Client(transport) as client:
            async with listen(client.session, resources_list_changed=True):
                assert runtime.subscriptions.active == 1
                if ending == "revoke":
                    with UnitOfWork(database) as unit:
                        unit.api_keys.update(READ, {"enabled": False})
                        unit.commit()
                elif ending == "stop":
                    runtime.alive = False
                if ending != "unsubscribe":
                    await wait_until_closed(runtime)
            await wait_until_closed(runtime)


@pytest.mark.asyncio
async def test_rollback_is_silent_and_withdrawal_does_not_emit_private_uri(
    content_app, monkeypatch
):
    _, database = content_app
    app = configured_app(database, monkeypatch, require_auth=False)
    upload_skill_profiles(
        database, content=skill_package(), version="1.0.0", selection="remote"
    )
    publish_content_version(
        database, slug="demo", version="1.0.0", remote_public=True
    )
    async with asyncio.timeout(20), live_http(app) as url:
        async with Client(url + "/skills/") as client:
            uri = str((await client.list_resources())[0].uri)
            async with listen(
                client.session,
                resources_list_changed=True,
                resource_subscriptions=[uri],
            ) as events:
                pending = asyncio.create_task(anext(events))
                try:
                    with UnitOfWork(database) as unit:
                        skill = unit.skills.get_by_slug("demo")
                        version = unit.skill_versions.get(skill.id, "1.0.0")
                        variant = unit.skill_variants.get(version.id, "remote", "")
                        variant.status = "archived"
                        unit.session.flush()
                        unit.session.rollback()
                    await asyncio.sleep(0.1)
                    assert not pending.done()
                    assert await client.list_resources()
                    withdraw_remote_variant(database, slug="demo", version="1.0.0")
                    assert isinstance(
                        await asyncio.wait_for(pending, 3), ResourcesListChanged
                    )
                    assert await client.list_resources() == []
                    # 撤销后不再发送带该 URI 的更新，也不能读到撤销内容。
                    pending = asyncio.create_task(anext(events))
                    await asyncio.sleep(0.1)
                    assert not pending.done()
                    with pytest.raises(MCPError, match="Resource not found"):
                        await client.read_resource(uri)
                finally:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
            await wait_until_closed(app.state.skills_mcp_runtime)
