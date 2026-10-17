"""Apps 真实协议订阅复用发布审核、匿名边界和撤权清理。"""

import asyncio
import json

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.client.subscriptions import listen
from mcp.shared.subscriptions import ResourcesListChanged, ToolsListChanged
from test_apps_mcp_gateway import bound_source, gateway
from test_gateway_client_capabilities import live_http
from test_registry_subscriptions import wait_until_closed
from test_skill_content_workflow import READ, WRITE, content_app

from app.storage.unit_of_work import UnitOfWork
from app.services.config_service import ConfigService


async def collect_pair(events):
    async with asyncio.timeout(5):
        result = [await anext(events), await anext(events)]
    assert {type(event) for event in result} == {ResourcesListChanged, ToolsListChanged}


@pytest.mark.asyncio
@pytest.mark.parametrize("public", [False, True])
async def test_app_draft_publish_withdraw_visibility(
    content_app, monkeypatch, tmp_path, public
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path, require_auth=False)
    async with asyncio.timeout(30), live_http(app) as url:
        async with httpx2.AsyncClient(
            base_url=url.removesuffix("/mcp"), headers={"Mcpcat-Key": WRITE}
        ) as admin:
            transport = StreamableHttpTransport(
                url + "/apps/", headers={"Mcpcat-Key": READ}
            )
            async with Client(url + "/apps/") as anonymous, Client(transport) as reader:
                async with listen(
                    anonymous.session,
                    resources_list_changed=True,
                    tools_list_changed=True,
                ) as public_events, listen(
                    reader.session,
                    resources_list_changed=True,
                    tools_list_changed=True,
                ) as reader_events:
                    public_next = asyncio.create_task(collect_pair(public_events))
                    reader_next = asyncio.create_task(collect_pair(reader_events))
                    try:
                        uploaded = await admin.post(
                            "/api/apps/upload",
                            data={
                                "slug": "demo",
                                "display_name": "Demo",
                                "version": "1.0.0",
                            },
                            files={
                                "file": (
                                    "app.json",
                                    json.dumps(
                                        {
                                            "view": {
                                                "type": "Heading",
                                                "content": "Fixture",
                                            }
                                        }
                                    ).encode(),
                                )
                            },
                        )
                        assert uploaded.status_code == 200, uploaded.text
                        await asyncio.sleep(0.1)
                        assert not public_next.done() and not reader_next.done()
                        published = await admin.post(
                            "/api/apps/demo/versions/1.0.0/publish",
                            json={"reviewed": True, "public": public},
                        )
                        assert published.status_code == 200, published.text
                        await reader_next
                        assert await reader.list_tools()
                        assert await reader.list_resources()
                        if public:
                            await public_next
                        else:
                            assert not public_next.done()
                        assert bool(await anonymous.list_resources()) == public
                        assert bool(await anonymous.list_tools()) == public
                        reader_next = asyncio.create_task(collect_pair(reader_events))
                        withdrawn = await admin.post(
                            "/api/apps/demo/versions/1.0.0/withdraw"
                        )
                        assert withdrawn.status_code == 200, withdrawn.text
                        await reader_next
                        assert await reader.list_resources() == []
                        assert await reader.list_tools() == []
                    finally:
                        for task in (public_next, reader_next):
                            task.cancel()
                        await asyncio.gather(
                            public_next, reader_next, return_exceptions=True
                        )
                await wait_until_closed(app.state.apps_mcp_runtime)


@pytest.mark.asyncio
async def test_app_key_revocation_closes_public_endpoint_subscription(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path, require_auth=False)
    async with asyncio.timeout(20), live_http(app) as url:
        transport = StreamableHttpTransport(
            url + "/apps/", headers={"Mcpcat-Key": READ}
        )
        async with Client(transport) as client:
            async with listen(client.session, tools_list_changed=True):
                assert app.state.apps_mcp_runtime.subscriptions.active == 1
                with UnitOfWork(database) as unit:
                    unit.api_keys.update(READ, {"enabled": False})
                    unit.commit()
                await wait_until_closed(app.state.apps_mcp_runtime)


@pytest.mark.asyncio
async def test_bound_source_becoming_private_notifies_without_executing_tool(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path, require_auth=False)
    invocations = []

    def value(value: int) -> int:
        invocations.append(value)
        return value + 1

    async with asyncio.timeout(30), live_http(app) as url:
        async with httpx2.AsyncClient(base_url=url.removesuffix("/mcp")) as admin:
            async with Client(url + "/apps/") as client:
                async with listen(
                    client.session, resources_list_changed=True, tools_list_changed=True
                ) as events:
                    await bound_source(
                        app, admin, value, source_public=True, app_public=True
                    )
                    await collect_pair(events)
                    assert len(await client.list_tools()) == 2
                    config = ConfigService.get_server_config("math")
                    assert ConfigService.update_server_config(
                        "math", {**config, "require_auth": True}
                    )
                    await collect_pair(events)
                    assert await client.list_tools() == []
                    assert await client.list_resources() == []
                    assert invocations == []
                await wait_until_closed(app.state.apps_mcp_runtime)
