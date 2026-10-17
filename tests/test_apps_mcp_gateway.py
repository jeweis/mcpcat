"""真实应用 HTTP 的 Apps Key、匿名审核、开关/冲突和私有缓存。"""

import base64
import io
import json
import zipfile

import httpx2
import pytest
from fastmcp import FastMCP
from fastmcp.tools import Tool
from test_catalog import server_info
from test_skill_content_workflow import READ, WRITE, Manager, content_app
from test_skills_mcp_gateway import rpc

from app.application import create_app
from app.core.config import settings
from app.services.config_service import ConfigService
from app.services.inspector_service import inspector_service
from app.services.market_service import MarketService
from app.storage.unit_of_work import UnitOfWork


def gateway(database, monkeypatch, tmp_path, **config):
    monkeypatch.setattr(settings, "mcpcat_apps_artifacts_path", str(tmp_path / "apps"))
    ConfigService.update_setting_section("apps", {"enabled": True, **config})
    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    return create_app(database=database, server_manager=Manager())


async def uploaded(client, *, public=False):
    wire = {
        "view": {"type": "Heading", "content": "{{ message }}"},
        "state": {"message": "Hello"},
    }
    response = await client.post(
        "/api/apps/upload",
        headers={"Mcpcat-Key": WRITE},
        data={"slug": "demo", "display_name": "Demo", "version": "1.0.0"},
        files={"file": ("app.json", json.dumps(wire).encode())},
    )
    assert response.status_code == 200, response.text
    response = await client.post(
        "/api/apps/demo/versions/1.0.0/publish",
        headers={"Mcpcat-Key": WRITE},
        json={"reviewed": True, "public": public},
    )
    assert response.status_code == 200, response.text


async def bound_source(app, client, function, *, source_public=False, app_public=False):
    """配置真实内存 MCP 工具，不执行夹具命令。"""
    ConfigService.add_server_to_config(
        "math",
        {
            "type": "stdio",
            "command": "fixture-not-run",
            "require_auth": not source_public,
        },
    )
    source = FastMCP("math")
    source.add_tool(Tool.from_function(function, name="value"))
    app.state.server_manager.server_info = {"math": server_info(source)}
    wire = {
        "view": {
            "type": "Button",
            "label": "Increment",
            "onClick": {
                "action": "toolCall",
                "tool": "value",
                "arguments": {"value": 7},
            },
        }
    }
    response = await client.post(
        "/api/apps/upload",
        headers={"Mcpcat-Key": WRITE},
        data={"slug": "bound", "display_name": "Bound", "version": "1.0.0"},
        files={"file": ("app.json", json.dumps(wire).encode())},
    )
    assert response.status_code == 200, response.text
    response = await client.put(
        "/api/apps/bound/versions/1.0.0/bindings",
        headers={"Mcpcat-Key": WRITE},
        json={
            "bindings": [
                {"alias": "value", "server_name": "math", "tool_name": "value"}
            ]
        },
    )
    assert response.status_code == 200, response.text
    response = await client.post(
        "/api/apps/bound/versions/1.0.0/publish",
        headers={"Mcpcat-Key": WRITE},
        json={"reviewed": True, "public": app_public},
    )
    assert response.status_code == 200, response.text
    tools = (await rpc(client, "tools/list", path="apps")).json()["result"]["tools"]
    return next(tool for tool in tools if tool["_meta"]["ui"]["visibility"] == ["app"])[
        "name"
    ]


@pytest.mark.asyncio
async def test_http_callback_uses_real_key_and_stops_after_revocation(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path)
    invocations = []

    def value(value: int) -> int:
        invocations.append(value)
        return value + 1

    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            name = await bound_source(app, client, value)
            result = await rpc(
                client,
                "tools/call",
                {"name": name, "arguments": {"value": 7}},
                path="apps",
            )
            assert result.json()["result"]["structuredContent"] == {
                "result": 8
            }, result.text
            assert invocations == [7]
            with UnitOfWork(database) as unit:
                unit.api_keys.update(READ, {"enabled": False})
                unit.commit()
            denied = await rpc(
                client,
                "tools/call",
                {"name": name, "arguments": {"value": 9}},
                path="apps",
            )
            assert denied.status_code == 401
            assert invocations == [7]


@pytest.mark.asyncio
@pytest.mark.parametrize("source_public", [False, True])
async def test_anonymous_app_callback_requires_source_public_review(
    content_app, monkeypatch, tmp_path, source_public
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path, require_auth=False)
    invocations = []

    def value(value: int) -> int:
        invocations.append(value)
        return value + 1

    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            name = await bound_source(
                app, client, value, source_public=source_public, app_public=True
            )
            tools = (await rpc(client, "tools/list", key=None, path="apps")).json()[
                "result"
            ]["tools"]
            assert bool(tools) == source_public
            result = (
                await rpc(
                    client,
                    "tools/call",
                    {"name": name, "arguments": {"value": 7}},
                    key=None,
                    path="apps",
                )
            ).json()
            if source_public:
                assert result["result"]["structuredContent"] == {"result": 8}, result
                assert invocations == [7]
            else:
                assert result.get("error") or result.get("result", {}).get(
                    "isError"
                ), result
                assert not invocations


@pytest.mark.asyncio
async def test_http_key_revocation_inside_tool_returns_partial_diagnostic(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path)
    invocations = []

    def revoke(value: int) -> int:
        invocations.append(value)
        with UnitOfWork(database) as unit:
            unit.api_keys.update(READ, {"enabled": False})
            unit.commit()
        return value + 1

    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            name = await bound_source(app, client, revoke)
            response = await rpc(
                client,
                "tools/call",
                {"name": name, "arguments": {"value": 7}},
                path="apps",
            )
            payload = response.json()
            assert payload["result"]["isError"], payload
            assert "部分操作可能已完成" in json.dumps(payload, ensure_ascii=False)
            assert invocations == [7]
            response = await rpc(
                client,
                "tools/call",
                {"name": name, "arguments": {"value": 9}},
                path="apps",
            )
            assert response.status_code == 401
            assert invocations == [7]


@pytest.mark.asyncio
async def test_http_apps_open_read_renderer_and_private_cache(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            await uploaded(client)
            assert (
                await rpc(client, "tools/list", key=None, path="apps")
            ).status_code == 401
            listed = await rpc(client, "tools/list", path="apps")
            tool = listed.json()["result"]["tools"][0]
            result = await rpc(
                client,
                "tools/call",
                {"name": tool["name"], "arguments": {}},
                path="apps",
            )
            assert not result.json()["result"].get("isError"), result.text
            assert result.json()["result"]["structuredContent"]["state"] == {
                "message": "Hello"
            }
            uri = tool["_meta"]["ui"]["resourceUri"]
            resource = await rpc(client, "resources/read", {"uri": uri}, path="apps")
            assert (
                resource.json()["result"]["contents"][0]["mimeType"]
                == "text/html;profile=mcp-app"
            )
            assert len(resource.json()["result"]["contents"][0]["text"]) > 6_000_000
            assert resource.headers["Cache-Control"] == "private, no-store"
            status = (
                await client.get(
                    "/api/admin/settings/apps-mcp", headers={"Mcpcat-Key": WRITE}
                )
            ).json()
            assert status["ready"] and not status["restart_required"]
            assert "apps" not in app.state.server_manager.server_info


@pytest.mark.asyncio
async def test_transport_public_does_not_publish_private_apps_or_accept_bad_key(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path, require_auth=False)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            await uploaded(client)
            listed = await rpc(client, "tools/list", key=None, path="apps")
            assert listed.json()["result"]["tools"] == []
            bad = await rpc(client, "tools/list", key="fixture-invalid", path="apps")
            assert bad.status_code == 401
            tools = (await rpc(client, "tools/list", path="apps")).json()["result"][
                "tools"
            ]
            assert len(tools) == 1
            uri = tools[0]["_meta"]["ui"]["resourceUri"]
            result = await rpc(
                client, "resources/read", {"uri": uri}, key=None, path="apps"
            )
            assert "error" in result.json()


@pytest.mark.asyncio
async def test_explicit_public_app_can_read_anonymously_and_withdraw_stops_uri(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path, require_auth=False)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            await uploaded(client, public=True)
            tools = (await rpc(client, "tools/list", key=None, path="apps")).json()[
                "result"
            ]["tools"]
            assert len(tools) == 1
            uri = tools[0]["_meta"]["ui"]["resourceUri"]
            assert (
                "error"
                not in (
                    await rpc(
                        client, "resources/read", {"uri": uri}, key=None, path="apps"
                    )
                ).json()
            )
            await client.post(
                "/api/apps/demo/versions/1.0.0/withdraw", headers={"Mcpcat-Key": WRITE}
            )
            assert (
                "error"
                in (
                    await rpc(
                        client, "resources/read", {"uri": uri}, key=None, path="apps"
                    )
                ).json()
            )


@pytest.mark.asyncio
async def test_disable_change_path_and_reserved_mount_cannot_take_over(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            response = await client.put(
                "/api/admin/settings/apps-mcp",
                headers={"Mcpcat-Key": WRITE},
                json={"enabled": False},
            )
            assert response.status_code == 200
            assert response.json()["ready"]
            assert "enabled" not in response.json()["config"]
            assert (await rpc(client, "tools/list", path="apps")).status_code == 200
            response = await client.put(
                "/api/admin/settings/apps-mcp",
                headers={"Mcpcat-Key": WRITE},
                json={"enabled": True, "path_name": "new-apps"},
            )
            assert response.status_code == 200
            assert response.json()["restart_required"] and not response.json()["ready"]
            assert (await rpc(client, "tools/list", path="apps")).status_code == 404


@pytest.mark.asyncio
async def test_apps_path_conflicts_with_old_catalog_and_unknown_config_not_echoed(
    content_app, monkeypatch, tmp_path
):
    app, database = content_app
    ConfigService.update_setting_section(
        "apps", {"enabled": False, "legacy_note": {"secret": "fixture-not-echoed"}}
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        response = await client.put(
            "/api/admin/settings/apps-mcp",
            headers={"Mcpcat-Key": WRITE},
            json={"enabled": True, "path_name": "mcpcat"},
        )
        assert response.status_code == 409
        response = await client.put(
            "/api/admin/settings/apps-mcp",
            headers={"Mcpcat-Key": WRITE},
            json={"path_name": "new-apps"},
        )
        assert response.status_code == 200
        assert "fixture-not-echoed" not in response.text
        assert ConfigService.get_setting_section("apps")["legacy_note"] == {
            "secret": "fixture-not-echoed"
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["service", "skills_mcp", "code_mode"])
async def test_apps_rejects_all_enabled_source_and_system_path_conflicts(
    content_app, monkeypatch, kind
):
    app, _ = content_app
    if kind == "service":
        ConfigService.add_server_to_config(
            "taken", {"type": "stdio", "command": "fixture-not-run"}
        )
    else:
        ConfigService.update_setting_section(
            kind, {"enabled": True, "path_name": "taken"}
        )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        response = await client.put(
            "/api/admin/settings/apps-mcp",
            headers={"Mcpcat-Key": WRITE},
            json={"enabled": True, "path_name": "taken"},
        )
        assert response.status_code == 409, response.text
        assert not ConfigService.get_setting_section("apps", {}).get("enabled", False)


@pytest.mark.asyncio
async def test_disabled_public_catalog_same_name_cannot_bypass_apps_key(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    ConfigService.update_setting_section(
        "catalog", {"enabled": False, "path_name": "apps", "require_auth": False}
    )
    app = gateway(database, monkeypatch, tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            assert (
                await rpc(client, "tools/list", key=None, path="mcpcat-apps")
            ).status_code == 401
            assert (
                await rpc(client, "tools/list", path="mcpcat-apps")
            ).status_code == 200


@pytest.mark.asyncio
async def test_html_zip_http_open_assets_and_original_download(
    content_app, monkeypatch, tmp_path
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path)
    script = b'window.fixture = "</script>";'
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "index.html",
            '<html><head><link rel="stylesheet" href="style.css"></head><body><img src="icon.png"><script src="app.js"></script></body></html>',
        )
        archive.writestr("style.css", "body{color:green}")
        archive.writestr("icon.png", b"image")
        archive.writestr("app.js", script)
    original = output.getvalue()
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            response = await client.post(
                "/api/apps/upload",
                headers={"Mcpcat-Key": WRITE},
                data={"slug": "html", "display_name": "HTML", "version": "1.0.0"},
                files={"file": ("app.zip", original)},
            )
            assert response.status_code == 200, response.text
            response = await client.post(
                "/api/apps/html/versions/1.0.0/publish",
                headers={"Mcpcat-Key": WRITE},
                json={"reviewed": True},
            )
            assert response.status_code == 200, response.text
            tools = (await rpc(client, "tools/list", path="apps")).json()["result"][
                "tools"
            ]
            assert len(tools) == 1
            opened = (
                await rpc(
                    client,
                    "tools/call",
                    {"name": tools[0]["name"], "arguments": {}},
                    path="apps",
                )
            ).json()
            assert opened["result"]["structuredContent"] == {
                "mcpcat": {"format": "html", "toolBindings": {}}
            }
            uri = tools[0]["_meta"]["ui"]["resourceUri"]
            data = (
                await rpc(client, "resources/read", {"uri": uri}, path="apps")
            ).json()["result"]["contents"][0]
            assert data["mimeType"] == "text/html;profile=mcp-app"
            assert base64.b64encode(script).decode() in data["text"]
            assert "data:image/png;base64,aW1hZ2U=" in data["text"]
            download = await client.get(
                "/api/apps/html/versions/1.0.0/download", headers={"Mcpcat-Key": READ}
            )
            assert download.content == original
