"""正式 Catalog HTTP 的上传 Apps 打开、资源、回调、受众及即时撤销。"""

import json
from contextlib import asynccontextmanager

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from test_apps_mcp_gateway import bound_source
from test_skill_content_workflow import READ, content_app
from test_skills_mcp_gateway import rpc

from app.application import create_app
from app.core.config import settings
from app.models.code_mode_config import CodeModeConfig
from app.models.mcp_config import CatalogConfig
from app.services.code_mode_provider import CodeModeProvider
from app.services.config_service import ConfigService
from app.services.inspector_service import inspector_service
from app.services.market_service import MarketService
from app.services.server_manager import MCPServerManager
from app.storage.unit_of_work import UnitOfWork


@asynccontextmanager
async def catalog_gateway(database, monkeypatch, tmp_path, *, public=False):
    monkeypatch.setattr(settings, "mcpcat_apps_artifacts_path", str(tmp_path / "apps"))
    ConfigService.update_setting_section(
        "apps", {"enabled": True, "require_auth": not public}
    )
    ConfigService.update_setting_section(
        "catalog", {"enabled": True, "require_auth": not public}
    )
    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    app = create_app(database=database, server_manager=MCPServerManager())
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            yield app, client


async def opened(client, callback):
    """普通 Catalog 列表只给打开动作，原 synthetic 名称继续存在。"""
    tools = (await rpc(client, "tools/list", path="mcpcat")).json()["result"]["tools"]
    assert {"search_tools", "call_tool"} <= {tool["name"] for tool in tools}
    assert callback not in {tool["name"] for tool in tools}
    opening = next(tool for tool in tools if tool["name"].startswith("open_app_"))
    response = await rpc(
        client, "tools/call", {"name": opening["name"], "arguments": {}}, path="mcpcat"
    )
    assert not response.json()["result"].get("isError", False), response.text
    return opening, response.json()["result"]


async def excludes_code_mode(manager, callback, opening):
    provider = CodeModeProvider(
        manager,
        authorize=lambda: True,
        config_loader=lambda: CodeModeConfig(enabled=True),
        catalog_loader=CatalogConfig,
    )
    assert all(
        tool.name not in {callback, opening} for tool in await provider.list_tools()
    )
    assert await provider.get_tool(callback) is None
    assert await provider.get_tool(opening) is None


@pytest.mark.asyncio
async def test_catalog_uploaded_app_roundtrip_and_callback_hidden_from_search_and_code_mode(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    calls = []

    def value(value: int) -> int:
        calls.append(value)
        return value + 1

    async with catalog_gateway(database, monkeypatch, tmp_path) as (app, client):
        callback = await bound_source(app, client, value)
        opening, result = await opened(client, callback)
        uri = opening["_meta"]["ui"]["resourceUri"]
        catalog = (
            await rpc(client, "resources/read", {"uri": uri}, path="mcpcat")
        ).json()["result"]
        direct = (
            await rpc(client, "resources/read", {"uri": uri}, path="apps")
        ).json()["result"]
        assert catalog["contents"] == direct["contents"]
        assert catalog["contents"][0]["mimeType"] == "text/html;profile=mcp-app"
        assert callback in str(result["structuredContent"])
        denied = await rpc(
            client,
            "tools/call",
            {
                "name": "call_tool",
                "arguments": {"name": callback, "arguments": {"value": 99}},
            },
            path="mcpcat",
        )
        assert denied.json()["result"]["isError"] and not calls
        search = await rpc(
            client,
            "tools/call",
            {"name": "search_tools", "arguments": {"query": callback}},
            path="mcpcat",
        )
        assert callback not in str(search.json()["result"].get("structuredContent", {}))
        called = await rpc(
            client,
            "tools/call",
            {"name": callback, "arguments": {"value": 7}},
            path="mcpcat",
        )
        assert called.json()["result"]["structuredContent"] == {"result": 8}
        assert calls == [7]
        await excludes_code_mode(app.state.server_manager, callback, opening["name"])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_native_clients_use_uploaded_apps_through_catalog(
    content_app, monkeypatch, tmp_path, mode
):
    _, database = content_app

    def value(value: int) -> int:
        return value + 1

    async with catalog_gateway(database, monkeypatch, tmp_path) as (app, management):
        callback = await bound_source(app, management, value)
        transport = StreamableHttpTransport(
            "http://fixture/mcp/mcpcat/",
            headers={"Mcpcat-Key": READ},
            httpx_client_factory=lambda **kwargs: httpx2.AsyncClient(
                **kwargs, transport=httpx2.ASGITransport(app=app)
            ),
        )
        async with Client(transport, mode=mode) as client:
            opening = next(
                tool
                for tool in await client.list_tools()
                if tool.name.startswith("open_app_")
            )
            opened_app = await client.call_tool(opening.name, {})
            assert not opened_app.is_error and callback in str(
                opened_app.structured_content
            )
            assert (
                "Interactive rendering requires an MCP Apps host"
                in opened_app.content[0].text
            )
            content = await client.read_resource(opening.meta["ui"]["resourceUri"])
            assert content[0].mime_type == "text/html;profile=mcp-app"
            result = await client.call_tool(callback, {"value": 4})
            assert not result.is_error and result.structured_content == {"result": 5}


@pytest.mark.asyncio
async def test_legacy_catalog_client_accepts_fixed_apps_json_response(
    content_app, monkeypatch
):
    _, database = content_app
    ConfigService.update_setting_section("apps", {"enabled": False})
    ConfigService.update_setting_section("catalog", {"enabled": True})
    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    app = create_app(database=database, server_manager=MCPServerManager())
    observed = []

    async def record(response):
        if (
            response.request.method == "POST"
            and json.loads(response.request.content).get("method") == "tools/list"
        ):
            observed.append(response.headers["content-type"])

    def client_factory(**options):
        options["event_hooks"] = {"response": [record]}
        return httpx2.AsyncClient(**options, transport=httpx2.ASGITransport(app=app))

    async with app.router.lifespan_context(app):
        transport = StreamableHttpTransport(
            "http://fixture/mcp/mcpcat/",
            headers={"Mcpcat-Key": READ},
            httpx_client_factory=client_factory,
        )
        async with Client(transport, mode="legacy") as client:
            names = {tool.name for tool in await client.list_tools()}
            assert {"search_tools", "call_tool"} <= names
    assert observed and all(value.startswith("application/json") for value in observed)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["source-stop", "app-withdraw", "key-revoke"])
async def test_catalog_open_snapshot_cannot_extend_permission(
    content_app, monkeypatch, tmp_path, change
):
    _, database = content_app
    calls = []

    def value(value: int) -> int:
        calls.append(value)
        return value

    async with catalog_gateway(database, monkeypatch, tmp_path) as (app, client):
        callback = await bound_source(app, client, value)
        opening, _ = await opened(client, callback)
        if change == "source-stop":
            app.state.server_manager.server_info["math"]["status"] = "stopped"
        elif change == "app-withdraw":
            from app.services.app_publication_service import withdraw_app_version

            withdraw_app_version(database, slug="bound", version="1.0.0")
        else:
            with UnitOfWork(database) as unit:
                unit.api_keys.update(READ, {"enabled": False})
                unit.commit()
        response = await rpc(
            client,
            "tools/call",
            {"name": callback, "arguments": {"value": 9}},
            path="mcpcat",
        )
        assert (
            response.status_code == 401
            or response.json().get("error")
            or response.json().get("result", {}).get("isError")
        )
        resource = await rpc(
            client,
            "resources/read",
            {"uri": opening["_meta"]["ui"]["resourceUri"]},
            path="mcpcat",
        )
        assert resource.status_code == 401 or "error" in resource.json()
        assert not calls


@pytest.mark.asyncio
async def test_public_catalog_does_not_make_private_uploaded_app_public(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    async with catalog_gateway(database, monkeypatch, tmp_path, public=True) as (
        app,
        client,
    ):
        callback = await bound_source(
            app, client, lambda value: value, source_public=True, app_public=False
        )
        opening, _ = await opened(client, callback)
        anonymous = await rpc(client, "tools/list", path="mcpcat", key=None)
        assert opening["name"] not in {
            tool["name"] for tool in anonymous.json()["result"]["tools"]
        }
        resource = await rpc(
            client,
            "resources/read",
            {"uri": opening["_meta"]["ui"]["resourceUri"]},
            path="mcpcat",
            key=None,
        )
        assert "error" in resource.json()
