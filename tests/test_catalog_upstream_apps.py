"""正式 Catalog HTTP 的同名上游 App 打开/资源/回调与当前身份检查。"""

import base64
import json
from contextlib import AsyncExitStack

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from test_catalog_uploaded_apps import catalog_gateway, excludes_code_mode
from test_skill_content_workflow import READ
from test_skill_content_workflow import content_app as _content_app
from test_skills_mcp_gateway import rpc
from test_upstream_apps_proxy import ASSET, URI, proxy_targets, source_app

from app.models.code_mode_config import CodeModeConfig
from app.models.mcp_config import CatalogConfig
from app.services.catalog_app_request import catalog_request
from app.services.code_mode_provider import CodeModeProvider
from app.services.config_service import ConfigService
from app.storage.unit_of_work import UnitOfWork

content_app = _content_app


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["alpha", "catapp_source"])
async def test_app_classification_does_not_revive_ambiguous_catalog_names(
    content_app, monkeypatch, tmp_path, prefix
):
    _, database = content_app
    calls = []
    async with catalog_gateway(database, monkeypatch, tmp_path) as (app, client):
        source, _ = add_source(app, prefix)

        @source.tool(name="beta__open_html")
        def wrong_target() -> str:
            calls.append("wrong")
            return "must not run"

        add_source(app, prefix + "__beta")
        name = prefix + "__beta__open_html"
        listed = (await rpc(client, "tools/list", path="mcpcat")).json()["result"][
            "tools"
        ]
        assert name not in {tool["name"] for tool in listed}
        service = app.state.server_manager.catalog_service
        with catalog_request(READ):
            assert await service._provider.get_tool(name) is None
            assert await service._upstream_apps.get_tool(name) is None
            code = CodeModeProvider(
                app.state.server_manager,
                authorize=lambda: True,
                config_loader=lambda: CodeModeConfig(
                    enabled=True, allowed_services=[prefix]
                ),
                catalog_loader=CatalogConfig,
            )
            assert await code.get_tool(name) is None
            assert name not in {tool.name for tool in await code.list_tools()}
        denied = await rpc(
            client,
            "tools/call",
            {"name": "call_tool", "arguments": {"name": name, "arguments": {}}},
            path="mcpcat",
        )
        assert denied.json()["result"]["isError"] and not calls


def add_source(app, name, *, public=False, server=None):
    """只登记可信内存夹具，不启动配置里的 URL 或用户进程。"""
    fixture_server, state = source_app(name)
    server = server or fixture_server
    config = {
        "type": "streamable-http",
        "url": "http://unused.fixture/mcp",
        "expose_in_catalog": True,
        "require_auth": not public,
    }
    assert ConfigService.add_server_to_config(name, config)
    app.state.server_manager.server_info[name] = {
        "status": "running",
        "config": config,
        "mcp": server,
    }
    app.state.apps_mcp_runtime.register_source(name)
    return server, state


def relay_config(text):
    return json.loads(
        text.split('id="mcpcat-source-config">', 1)[1].split("</script>", 1)[0]
    )


async def verify_source_roundtrip(app, client, opening, name):
    """每次真实读取与交互后核对当前来源，不只比较名称。"""
    assert opening.meta["fixture/tool"] == name
    result = await client.call_tool_mcp(opening.name, {})
    assert result.structured_content == {"source": name, "count": 0}
    assert result.meta["fixture/result"] == name
    uri = opening.meta["ui"]["resourceUri"]
    assert uri != URI
    resource = await client.read_resource_mcp(uri)
    config = relay_config(resource.contents[0].text)
    assert base64.b64decode(config["html"]) == f"<!doctype html><p>{name}</p>".encode()
    assert resource.contents[0].meta["fixture/resource"] == name
    assert resource.meta["fixture/read"] == name
    callback = config["tools"]["save"]
    await excludes_code_mode(app.state.server_manager, callback, opening.name)
    called = await client.call_tool(callback, {"increment": 1})
    assert called.data == {"source": name, "count": 1}
    token = base64.urlsafe_b64encode(ASSET.encode()).decode().rstrip("=")
    asset_uri = uri.rsplit("/", 1)[0] + "/" + token
    asset = await client.read_resource_mcp(asset_uri)
    assert asset.contents[0].mime_type == "application/x-fixture"
    assert base64.b64decode(asset.contents[0].blob) == b"\x00\xff" + name.encode()
    return callback


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_catalog_two_same_name_upstream_apps_roundtrip(
    content_app, monkeypatch, tmp_path, mode
):
    _, database = content_app
    async with catalog_gateway(database, monkeypatch, tmp_path) as (app, _):
        states = {name: add_source(app, name)[1] for name in ("alpha", "beta")}
        transport = StreamableHttpTransport(
            "http://fixture/mcp/mcpcat/",
            headers={"Mcpcat-Key": READ},
            httpx_client_factory=lambda **options: httpx2.AsyncClient(
                **options, transport=httpx2.ASGITransport(app=app)
            ),
        )
        async with Client(transport, mode=mode) as client:
            listed = await client.list_tools()
            names = {item.name for item in listed}
            assert {
                "search_tools",
                "call_tool",
                "alpha__open_html",
                "beta__open_html",
            } <= names
            assert not any(
                name.startswith("catapp_") or name.endswith("__save") for name in names
            )
            callbacks = []
            for name in states:
                opening = next(
                    item for item in listed if item.name == name + "__open_html"
                )
                callbacks.append(
                    await verify_source_roundtrip(app, client, opening, name)
                )
            assert callbacks[0] != callbacks[1]
            assert all(state["count"] == 1 for state in states.values())


@pytest.mark.asyncio
async def test_catalog_prefab_dynamic_payload_stays_unmodified(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    async with catalog_gateway(database, monkeypatch, tmp_path) as (app, client):
        add_source(app, "alpha")
        tools = (await rpc(client, "tools/list", path="mcpcat")).json()["result"][
            "tools"
        ]
        opening = next(item for item in tools if item["name"] == "alpha__open_prefab")
        result = (
            await rpc(
                client,
                "tools/call",
                {"name": opening["name"], "arguments": {}},
                path="mcpcat",
            )
        ).json()["result"]
        original = result["structuredContent"]
        uri = opening["_meta"]["ui"]["resourceUri"]
        renderer = (
            await rpc(client, "resources/read", {"uri": uri}, path="mcpcat")
        ).json()["result"]
        config = relay_config(renderer["contents"][0]["text"])
        callback = config["tools"]["save"]
        await rpc(
            client,
            "tools/call",
            {"name": callback, "arguments": {"increment": 4}},
            path="mcpcat",
        )
        changed = (
            await rpc(
                client,
                "tools/call",
                {"name": opening["name"], "arguments": {}},
                path="mcpcat",
            )
        ).json()["result"]["structuredContent"]
        assert original != changed
        assert "save" in str(original) and "catapp_" not in str(original)
        assert "4" in str(changed)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_catalog_uses_configured_upstream_auth_not_gateway_key(
    content_app, monkeypatch, tmp_path, mode
):
    _, database = content_app
    source, state = source_app("protected")
    upstream = source.http_app(path="/mcp", json_response=True)
    observed = []

    async def protected(scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            observed.append(headers)
            assert headers.get(b"authorization") == b"Bearer fixture-upstream-only"
            assert b"mcpcat-key" not in headers
        await upstream(scope, receive, send)

    proxies = proxy_targets(
        monkeypatch,
        {"http://protected.fixture/mcp": protected},
        headers={"Authorization": "Bearer fixture-upstream-only"},
    )
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(upstream.lifespan(upstream))
        app, _ = await stack.enter_async_context(
            catalog_gateway(database, monkeypatch, tmp_path)
        )
        add_source(app, "protected", server=proxies["http://protected.fixture/mcp"])
        transport = StreamableHttpTransport(
            "http://fixture/mcp/mcpcat/",
            headers={"Mcpcat-Key": READ},
            httpx_client_factory=lambda **options: httpx2.AsyncClient(
                **options, transport=httpx2.ASGITransport(app=app)
            ),
        )
        async with Client(transport, mode=mode) as client:
            tools = await client.list_tools()
            opening = next(
                tool for tool in tools if tool.name == "protected__open_html"
            )
            ui = await client.read_resource(opening.meta["ui"]["resourceUri"])
            callback = relay_config(ui[0].text)["tools"]["save"]
            result = await client.call_tool(callback, {"increment": 2})
            assert result.data == {"source": "protected", "count": 2}
        assert observed and state["count"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["key-revoke", "source-stop", "source-replace"])
async def test_catalog_upstream_old_tool_cannot_extend_access(
    content_app, monkeypatch, tmp_path, change
):
    _, database = content_app
    async with catalog_gateway(database, monkeypatch, tmp_path) as (app, client):
        _, state = add_source(app, "alpha")
        listed = (await rpc(client, "tools/list", path="mcpcat")).json()["result"][
            "tools"
        ]
        opening = next(item for item in listed if item["name"] == "alpha__open_html")
        uri = opening["_meta"]["ui"]["resourceUri"]
        read = (
            await rpc(client, "resources/read", {"uri": uri}, path="mcpcat")
        ).json()["result"]
        callback = relay_config(read["contents"][0]["text"])["tools"]["save"]
        with catalog_request(READ):
            tool = (
                await app.state.server_manager.catalog_service._upstream_apps.get_tool(
                    callback
                )
            )
        assert tool is not None
        if change == "key-revoke":
            with UnitOfWork(database) as unit:
                unit.api_keys.update(READ, {"enabled": False})
                unit.commit()
        elif change == "source-stop":
            app.state.server_manager.server_info["alpha"]["status"] = "stopped"
        else:
            app.state.server_manager.server_info["alpha"]["mcp"] = source_app(
                "replacement"
            )[0]
        with catalog_request(READ), pytest.raises(ToolError):
            await tool.run({"increment": 9})
        assert state["count"] == 0
        denied = await rpc(client, "resources/read", {"uri": uri}, path="mcpcat")
        assert denied.status_code == 401 or "error" in denied.json()


@pytest.mark.asyncio
async def test_public_catalog_does_not_publish_private_upstream_app(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    async with catalog_gateway(database, monkeypatch, tmp_path, public=True) as (
        app,
        client,
    ):
        add_source(app, "private")
        add_source(app, "public", public=True)
        response = await rpc(client, "tools/list", path="mcpcat", key=None)
        names = {item["name"] for item in response.json()["result"]["tools"]}
        assert "public__open_html" in names and "private__open_html" not in names
