"""Apps 中心来源发现：只读、分页、同名隔离与实时身份/代际。"""

from unittest.mock import AsyncMock

import httpx2
import pytest
from fastmcp import Client, FastMCP
from fastmcp.apps import AppConfig
from test_skill_content_workflow import READ, content_app

from app.services import app_discovery
from app.services.config_service import ConfigService


@pytest.fixture
def discovery(content_app, monkeypatch):
    app, database = content_app
    calls = []
    server = FastMCP("source")

    @server.tool(app=AppConfig(resourceUri="ui://shared/clock"))
    def clock() -> str:
        calls.append("tool")
        return "never called"

    @server.resource("ui://shared/clock")
    def content() -> str:
        calls.append("resource")
        return "never read"

    configs = {
        name: {"enabled": True, "require_auth": True, "headers": {"Secret": "hidden"}}
        for name in ["one", "two", "stopped", "system"]
    }
    app.state.server_manager.server_info = {
        name: {"mcp": server, "status": "stopped" if name == "stopped" else "running"}
        for name in configs
    }
    app.state.server_manager.server_info["system"]["gateway_component"] = "apps"
    monkeypatch.setattr(ConfigService, "get_server_config", staticmethod(configs.get))
    monkeypatch.setattr(app_discovery, "_client", lambda *args: Client(server))
    client = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
    )
    return client, configs, app.state.server_manager, calls


@pytest.mark.asyncio
async def test_sources_and_same_named_apps_are_read_only_and_separate(discovery):
    client, _, _, calls = discovery
    async with client:
        sources = await client.get(
            "/api/apps/discovery/sources", headers={"Mcpcat-Key": READ}
        )
        assert sources.status_code == 200
        assert [v["server_name"] for v in sources.json()] == ["one", "stopped", "two"]
        assert "hidden" not in sources.text and "headers" not in sources.text
        for name in ["one", "two"]:
            response = await client.get(
                f"/api/apps/discovery/sources/{name}", headers={"Mcpcat-Key": READ}
            )
            assert response.status_code == 200, response.text
            value = response.json()
            assert value["server_name"] == name
            assert value["items"][0]["name"] == "clock"
            assert value["items"][0]["resource_uri"] == "ui://shared/clock"
            assert value["complete"] and value["supported"]
        assert (
            await client.get("/api/apps", headers={"Mcpcat-Key": READ})
        ).json() == []
        assert calls == []


@pytest.mark.asyncio
async def test_stopped_unknown_and_invalid_identity_do_not_discover(discovery):
    client, _, _, calls = discovery
    async with client:
        for name, code in [("stopped", 409), ("system", 404), ("missing", 404)]:
            response = await client.get(
                f"/api/apps/discovery/sources/{name}", headers={"Mcpcat-Key": READ}
            )
            assert response.status_code == code
        assert (await client.get("/api/apps/discovery/sources")).status_code == 401
        assert calls == []


@pytest.mark.asyncio
async def test_source_change_during_query_rejects_late_result(discovery, monkeypatch):
    client, configs, _, _ = discovery
    original = app_discovery._pages

    async def changed(connection):
        result = await original(connection)
        configs["one"]["headers"] = {"Secret": "replacement"}
        return result

    monkeypatch.setattr(app_discovery, "_pages", changed)
    async with client:
        result = await client.get(
            "/api/apps/discovery/sources/one", headers={"Mcpcat-Key": READ}
        )
        assert result.status_code == 409
        assert "replacement" not in result.text and "clock" not in result.text


@pytest.mark.asyncio
async def test_revoked_identity_does_not_keep_result_for_public_source(
    discovery, monkeypatch
):
    client, configs, _, _ = discovery
    configs["one"]["require_auth"] = False
    original = app_discovery._pages

    async def revoked(connection):
        result = await original(connection)
        monkeypatch.setattr(
            app_discovery.security_service, "verify_api_key", lambda value: None
        )
        return result

    monkeypatch.setattr(app_discovery, "_pages", revoked)
    async with client:
        response = await client.get(
            "/api/apps/discovery/sources/one", headers={"Mcpcat-Key": READ}
        )
        assert response.status_code == 403
        assert "clock" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,code", [(ValueError("private-secret"), 502), (TimeoutError(), 504)]
)
async def test_failure_is_safe_and_does_not_poison_other_source(
    discovery, monkeypatch, error, code
):
    client, _, _, _ = discovery
    original = app_discovery._pages
    monkeypatch.setattr(app_discovery, "_pages", AsyncMock(side_effect=error))
    async with client:
        failed = await client.get(
            "/api/apps/discovery/sources/one", headers={"Mcpcat-Key": READ}
        )
        assert failed.status_code == code
        assert "private-secret" not in failed.text
        monkeypatch.setattr(app_discovery, "_pages", original)
        assert (
            await client.get(
                "/api/apps/discovery/sources/two", headers={"Mcpcat-Key": READ}
            )
        ).status_code == 200


@pytest.mark.asyncio
async def test_page_traversal_dedup_and_boundaries(monkeypatch):
    item = {"name": "clock", "_meta": {"ui": {"resourceUri": "ui://clock"}}}
    listing = AsyncMock(
        side_effect=[
            {"items": [], "nextCursor": "page2", "supported": True},
            {"items": [item, item], "nextCursor": None, "supported": True},
        ]
    )
    monkeypatch.setattr(app_discovery, "listing", listing)
    result = await app_discovery._pages(object())
    assert result["complete"] and len(result["items"]) == 1
    assert listing.call_args.args[-1] == "page2"
    listing.side_effect = [{"items": [], "nextCursor": "repeat", "supported": True}] * 2
    with pytest.raises(ValueError, match="游标"):
        await app_discovery._pages(object())
    listing.side_effect = [
        {"items": [], "nextCursor": str(i), "supported": True} for i in range(20)
    ]
    assert not (await app_discovery._pages(object()))["complete"]
