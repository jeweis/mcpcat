"""真实应用/HTTP/官方 CodeMode 路径，不用伪造 SDK 执行上下文。"""

import asyncio
import json

import httpx2
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.tools import Tool
from test_catalog import server_info
from test_skill_content_workflow import READ, WRITE, Manager, content_app
from test_skills_mcp_gateway import rpc

from app.application import create_app
from app.services.code_mode_provider import GATEWAY_COMPONENT_META
from app.services.config_service import ConfigService
from app.services.inspector_service import inspector_service
from app.services.market_service import MarketService
from app.services.mcp_factory import MCPServerFactory
from app.services.server_manager import MCPServerManager
from app.storage.unit_of_work import UnitOfWork


def gateway(database, monkeypatch, tool=None, **config):
    ConfigService.update_setting_section("code_mode", {"enabled": True, **config})
    ConfigService.add_server_to_config(
        "math",
        {"type": "stdio", "command": "fixture-not-run", "expose_in_catalog": True},
    )
    source = FastMCP("math")

    def increment(value: int) -> int:
        return value + 1

    source.add_tool(Tool.from_function(tool or increment, name="value"))
    monkeypatch.setattr(MCPServerFactory, "create_server", lambda name, config: source)
    manager = MCPServerManager()
    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    return create_app(database=database, server_manager=manager)


async def call(client, name, arguments, *, key=READ, path="code-mode"):
    return (
        await rpc(
            client,
            "tools/call",
            {"name": name, "arguments": arguments},
            key=key,
            path=path,
        )
    ).json()


@pytest.mark.asyncio
async def test_code_mode_live_http_schema_execute_and_original_catalog(
    content_app, monkeypatch
):
    _, database = content_app
    app = gateway(database, monkeypatch)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            assert (await rpc(client, key=None, path="code-mode")).status_code == 401
            tools = (await rpc(client, "tools/list", path="code-mode")).json()[
                "result"
            ]["tools"]
            assert {tool["name"] for tool in tools} == {
                "search",
                "get_schema",
                "execute",
            }
            assert all(
                tool["_meta"][GATEWAY_COMPONENT_META] == "code-mode" for tool in tools
            )
            assert "math__value" in json.dumps(
                await call(client, "search", {"query": "value"})
            )
            schema = await call(
                client, "get_schema", {"tools": ["math__value"], "detail": "full"}
            )
            assert not schema["result"].get("isError", False), schema
            assert '"value"' in schema["result"]["content"][0]["text"]
            result = await call(
                client,
                "execute",
                {
                    "code": 'a = await call_tool("math__value", {"value": 2})\nreturn a["result"] * 2'
                },
            )
            assert "error" not in result, result
            assert not result["result"].get("isError", False), result
            assert json.loads(result["result"]["content"][0]["text"]) == 6, result
            denied = await call(client, "math__value", {"value": 2})
            assert denied.get("error") or denied.get("result", {}).get(
                "isError"
            ), denied
            catalog_tools = (
                await app.state.server_manager.catalog_service.list_external_tools()
            )
            assert {tool.name for tool in catalog_tools} == {
                "search_tools",
                "call_tool",
            }
            catalog_http = (await rpc(client, "tools/list", path="mcpcat")).json()
            assert {tool["name"] for tool in catalog_http["result"]["tools"]} == {
                "search_tools",
                "call_tool",
            }
            status = (
                await client.get(
                    "/api/admin/settings/code-mode", headers={"Mcpcat-Key": WRITE}
                )
            ).json()
            assert status["ready"] is True
            assert status["restart_required"] is False


@pytest.mark.asyncio
async def test_actual_key_revoked_mid_execute_blocks_next_dispatch(
    content_app, monkeypatch
):
    _, database = content_app
    invocations = []

    def revoke(value: int):
        invocations.append(value)
        with UnitOfWork(database) as unit:
            unit.api_keys.update(READ, {"enabled": False})
            unit.commit()
        return value

    app = gateway(database, monkeypatch, tool=revoke)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            result = await call(
                client,
                "execute",
                {
                    "code": 'await call_tool("math__value", {"value": 1})\nreturn await call_tool("math__value", {"value": 2})'
                },
            )
            assert result.get("error") or result.get("result", {}).get(
                "isError"
            ), result
            assert invocations == [1]
            assert "not rolled back" in json.dumps(result)


@pytest.mark.asyncio
async def test_disable_endpoint_preserves_old_catalog_and_mounted_path(
    content_app, monkeypatch
):
    _, database = content_app
    app = gateway(database, monkeypatch)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://fixture",
            headers={"Mcpcat-Key": WRITE},
        ) as client:
            disabled = await client.put(
                "/api/admin/settings/code-mode", json={"enabled": False}
            )
            assert disabled.json()["ready"] is True
            assert (
                await rpc(client, "tools/list", path="code-mode")
            ).status_code == 200
            assert (
                await client.post(
                    "/api/servers",
                    json={
                        "name": "code-mode",
                        "config": {"type": "stdio", "command": "not-run"},
                    },
                )
            ).status_code == 409
            assert app.state.server_manager.catalog_service is not None


@pytest.mark.asyncio
async def test_custom_code_path_matching_disabled_public_catalog_still_requires_key(
    content_app, monkeypatch
):
    _, database = content_app
    ConfigService.update_setting_section(
        "catalog", {"enabled": False, "path_name": "shared", "require_auth": False}
    )
    app = gateway(database, monkeypatch, path_name="shared")
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            assert (
                await rpc(client, path="mcpcat-code-mode", key=None)
            ).status_code == 401
            assert "error" not in await call(
                client, "execute", {"code": "return 7"}, path="mcpcat-code-mode"
            )


@pytest.mark.asyncio
async def test_scope_restriction_cancels_live_http_execute_and_reuses_slot(
    content_app, monkeypatch
):
    _, database = content_app
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def slow(value: int) -> int:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    app = gateway(database, monkeypatch, tool=slow, limits={"principal_concurrency": 1})
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            pending = asyncio.create_task(
                call(
                    client,
                    "execute",
                    {"code": 'return await call_tool("math__value", {"value": 1})'},
                )
            )
            try:
                await asyncio.wait_for(entered.wait(), 3)
                response = await client.put(
                    "/api/admin/settings/code-mode",
                    headers={"Mcpcat-Key": WRITE},
                    json={"allowed_services": []},
                )
                assert response.status_code == 200
                assert cleaned.is_set()
                assert not app.state.code_mode_runtime.bridge.active
                assert not app.state.code_mode_runtime.bridge.retained
                await client.put(
                    "/api/admin/settings/code-mode",
                    headers={"Mcpcat-Key": WRITE},
                    json={"allowed_services": None},
                )
                result = await call(client, "execute", {"code": "return 9"})
                assert not result["result"].get("isError", False), result
                assert json.loads(result["result"]["content"][0]["text"]) == 9
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_source_stop_cancels_live_execute_and_hides_old_tools(
    content_app, monkeypatch
):
    _, database = content_app
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def slow(value: int) -> int:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    app = gateway(database, monkeypatch, tool=slow)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            pending = asyncio.create_task(
                call(
                    client,
                    "execute",
                    {"code": 'return await call_tool("math__value", {"value": 1})'},
                )
            )
            try:
                await asyncio.wait_for(entered.wait(), 3)
                manager = app.state.server_manager
                assert await asyncio.wait_for(manager.stop_server("math"), 3)
                assert cleaned.is_set()
                assert not app.state.code_mode_runtime.bridge.active
                denied = await call(
                    client,
                    "execute",
                    {
                        "code": 'return await call_tool("math__value", {"value": 1})',
                    },
                )
                assert denied["result"]["isError"], denied
                assert "math__value" not in json.dumps(
                    await call(client, "search", {"query": "value"})
                )
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_native_sdk_client_can_use_code_mode_and_catalog(
    content_app, monkeypatch, mode
):
    _, database = content_app
    app = gateway(database, monkeypatch)

    def transport(path):
        return StreamableHttpTransport(
            f"http://fixture/mcp/{path}/",
            headers={"Mcpcat-Key": READ},
            httpx_client_factory=lambda **kwargs: httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), **kwargs
            ),
        )

    async with app.router.lifespan_context(app):
        async with Client(transport("code-mode"), mode=mode, timeout=3) as client:
            assert client.protocol_version is not None
            if mode != "legacy":
                assert client.protocol_version == mode
            tools = await client.list_tools()
            assert {tool.name for tool in tools} == {"search", "get_schema", "execute"}
            result = await client.call_tool(
                "execute",
                {
                    "code": 'a = await call_tool("math__value", {"value": 2})\nreturn a["result"] * 2',
                },
            )
            assert json.loads(result.content[0].text) == 6
        async with Client(transport("mcpcat"), mode=mode, timeout=3) as client:
            assert {tool.name for tool in await client.list_tools()} == {
                "search_tools",
                "call_tool",
            }


@pytest.mark.asyncio
async def test_http_execute_quota_is_per_current_key(content_app, monkeypatch):
    _, database = content_app
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def slow(value: int) -> int:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    app = gateway(database, monkeypatch, tool=slow, limits={"principal_concurrency": 1})
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            pending = asyncio.create_task(
                call(
                    client,
                    "execute",
                    {
                        "code": 'return await call_tool("math__value", {"value": 1})',
                    },
                )
            )
            try:
                await asyncio.wait_for(entered.wait(), 3)
                denied = await call(client, "execute", {"code": "return 9"})
                assert denied["result"]["isError"], denied
                assert "principal_concurrency" in json.dumps(denied)
                other = await call(client, "execute", {"code": "return 9"}, key=WRITE)
                assert json.loads(other["result"]["content"][0]["text"]) == 9
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            assert cleaned.is_set()
            assert not app.state.code_mode_runtime.bridge.active
            retry = await call(client, "execute", {"code": "return 10"})
            assert json.loads(retry["result"]["content"][0]["text"]) == 10
