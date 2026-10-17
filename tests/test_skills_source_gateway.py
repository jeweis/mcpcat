"""真实 Manager 创建的来源端点、工具目录、重建身份和 Catalog 回归。"""

import httpx2
import pytest
from fastapi import FastAPI
from fastmcp import FastMCP
from test_remote_skill_protocol import skill_package
from test_skill_content_workflow import READ, content_app
from test_skills_mcp_gateway import rpc

from app.application import create_app
from app.services.config_service import ConfigService
from app.services.inspector_service import inspector_service
from app.services.market_service import MarketService
from app.services.mcp_factory import MCPServerFactory
from app.services.mcp_route_conflicts import mcp_route_occupied
from app.services.server_manager import MCPProxyApp, MCPServerManager
from app.services.skill_profile_upload import upload_skill_profiles
from app.services.skill_variant_publication import publish_content_version
from app.storage.runtime_identities import current_endpoint_identity, endpoint_identity


def source_app(database, monkeypatch, *, catalog=False, independent=True):
    assert ConfigService.update_setting_section(
        "skills_mcp", {"enabled": True, "independent_enabled": independent}
    )
    assert ConfigService.update_setting_section("catalog", {"enabled": catalog})
    assert ConfigService.add_server_to_config(
        "alpha",
        {"type": "stdio", "command": "fixture-not-run", "expose_in_catalog": True},
    )
    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)

    def factory(name, config):
        server = FastMCP(name)

        @server.tool()
        def echo(message: str) -> str:
            return message

        return server

    monkeypatch.setattr(MCPServerFactory, "create_server", factory)
    return create_app(database=database, server_manager=MCPServerManager())


def publish_bound(database, kind="service", source="alpha", required=None):
    binding = endpoint_identity(database, kind, source if kind == "service" else "")
    upload_skill_profiles(
        database,
        content=skill_package(),
        version="1.0.0",
        selection="both",
        binding=binding,
        metadata={
            "binding_kind": kind,
            "source_name": source,
            "required_tools": required or ["echo"],
        },
    )
    publish_content_version(database, slug="demo", version="1.0.0")
    return binding


@pytest.mark.asyncio
async def test_live_tools_and_discovery_switch_preserve_original_tools(
    content_app, monkeypatch
):
    _, database = content_app
    app = source_app(database, monkeypatch)
    publish_bound(database)
    manager = app.state.server_manager
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            discovery = (await rpc(client, "server/discover", path="alpha")).json()[
                "result"
            ]
            assert (
                "io.modelcontextprotocol/skills"
                in discovery["capabilities"]["extensions"]
            )
            entry = (await rpc(client, path="alpha")).json()["result"]["skills"][0]
            assert (await rpc(client, path="skills")).json()["result"]["skills"] == []
            assert (
                await rpc(client, "skills/get", {"uri": entry["uri"]}, path="skills")
            ).json()["error"]["code"] == -32602
            assert (
                await client.get(
                    "/api/skill-variants/demo/connections", headers={"Mcpcat-Key": READ}
                )
            ).json()["connections"][0]["ready"] is True
            server = manager.server_info["alpha"]["mcp"]

            # 上游/静态同 URI 资源不得覆盖平台已发布内容。
            @server.resource(entry["uri"])
            def collision() -> str:
                return "wrong-source-content"

            collision_read = await rpc(
                client, "resources/read", {"uri": entry["uri"]}, path="alpha"
            )
            assert collision_read.json()["result"]["contents"][0]["text"].endswith(
                "# Original\r\n"
            )
            server.local_provider.remove_tool("echo")
            assert (await rpc(client, path="alpha")).json()["result"]["skills"] == []
            assert (
                await client.get(
                    "/api/skill-variants/demo/connections", headers={"Mcpcat-Key": READ}
                )
            ).json()["connections"][0]["ready"] is False
            assert (
                await rpc(client, "resources/read", {"uri": entry["uri"]}, path="alpha")
            ).json()["error"]["code"] == -32602
            assert ConfigService.update_setting_section(
                "skills_mcp", {"enabled": False}
            )
            disabled = (await rpc(client, "server/discover", path="alpha")).json()[
                "result"
            ]
            assert (
                "io.modelcontextprotocol/skills"
                in disabled["capabilities"]["extensions"]
            )
            assert (
                "io.modelcontextprotocol/ui" in disabled["capabilities"]["extensions"]
            )
            assert (
                await rpc(client, "resources/list", path="alpha")
            ).status_code == 200


@pytest.mark.asyncio
async def test_delete_recreate_reuses_route_but_not_frozen_source_identity(
    content_app, monkeypatch
):
    _, database = content_app
    app = source_app(database, monkeypatch)
    binding = publish_bound(database)
    manager = app.state.server_manager
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            entry = (await rpc(client, path="alpha")).json()["result"]["skills"][0]
            assert await manager.remove_server("alpha")
            assert current_endpoint_identity(database, "service", "alpha") is None
            assert await manager.add_and_mount_server(
                app, "alpha", {"type": "stdio", "command": "fixture-not-run"}
            )
            assert endpoint_identity(database, "service", "alpha") != binding
            assert (await rpc(client, path="alpha")).json()["result"]["skills"] == []
            assert (
                await rpc(client, "skills/get", {"uri": entry["uri"]}, path="alpha")
            ).json()["error"]["code"] == -32602
            assert (
                await client.get(
                    "/api/skills/demo/versions/1.0.0/download",
                    headers={"Mcpcat-Key": READ},
                )
            ).status_code == 200


@pytest.mark.asyncio
async def test_catalog_uses_synthetic_tools_not_service_bound_workflow(
    content_app, monkeypatch
):
    _, database = content_app
    app = source_app(database, monkeypatch, catalog=True)
    publish_bound(database, "catalog", required=["search_tools", "call_tool"])
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            entry = (await rpc(client, path="mcpcat")).json()["result"]["skills"][0]
            assert (await rpc(client, path="alpha")).json()["result"]["skills"] == []
            assert (
                await rpc(
                    client, "resources/read", {"uri": entry["uri"]}, path="mcpcat"
                )
            ).status_code == 200
            tools = (await rpc(client, "tools/list", path="mcpcat")).json()["result"][
                "tools"
            ]
            assert {tool["name"] for tool in tools} == {"search_tools", "call_tool"}


@pytest.mark.asyncio
async def test_restart_preserves_binding_and_stopped_source_is_hidden(
    content_app, monkeypatch
):
    _, database = content_app
    app = source_app(database, monkeypatch)
    binding = publish_bound(database)
    manager = app.state.server_manager
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            entry = (await rpc(client, path="alpha")).json()["result"]["skills"][0]
            assert await manager.stop_server("alpha")
            assert (
                await rpc(client, "skills/get", {"uri": entry["uri"]}, path="alpha")
            ).status_code == 503
            assert await manager.restart_server("alpha")
            assert endpoint_identity(database, "service", "alpha") == binding
            same = await rpc(client, "skills/get", {"uri": entry["uri"]}, path="alpha")
            assert same.json()["result"]["skill"] == entry


def test_owned_stale_proxy_route_not_external_conflict():
    app = FastAPI()
    manager = MCPServerManager()
    app.mount("/mcp/alpha", MCPProxyApp("alpha", manager))
    assert mcp_route_occupied(app, "alpha")
    assert not mcp_route_occupied(app, "alpha", owner=manager)
    assert mcp_route_occupied(app, "alpha", owner=MCPServerManager())


@pytest.mark.asyncio
async def test_public_source_still_requires_skill_review_and_write_cannot_read_draft(
    content_app, monkeypatch
):
    from test_skill_content_workflow import WRITE

    _, database = content_app
    app = source_app(database, monkeypatch)
    binding = publish_bound(database)
    config = ConfigService.get_server_config("alpha")
    assert ConfigService.update_server_config(
        "alpha", {**config, "require_auth": False}
    )
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            entry = (await rpc(client, path="alpha")).json()["result"]["skills"][0]
            assert (await rpc(client, path="alpha", key=None)).json()["result"][
                "skills"
            ] == []
            assert (
                await rpc(
                    client,
                    "resources/read",
                    {"uri": entry["uri"]},
                    path="alpha",
                    key=None,
                )
            ).json()["error"]["code"] == -32602
            upload_skill_profiles(
                database,
                content=skill_package(),
                version="1.0.1",
                selection="remote",
                binding=binding,
                metadata={
                    "binding_kind": "service",
                    "source_name": "alpha",
                    "required_tools": ["echo"],
                },
            )
            draft_uri = entry["uri"].replace("/1.0.0/", "/1.0.1/")
            assert (
                await rpc(
                    client, "skills/get", {"uri": draft_uri}, path="alpha", key=WRITE
                )
            ).json()["error"]["code"] == -32602
            publish_content_version(
                database, slug="demo", version="1.0.1", remote_public=True
            )
            assert (await rpc(client, path="alpha", key=None)).json()["result"][
                "skills"
            ][0]["uri"] == draft_uri


def test_config_import_removal_retires_identity_without_changing_survivors(content_app):
    _, database = content_app
    for name in ("alpha", "beta"):
        assert ConfigService.add_server_to_config(
            name, {"type": "stdio", "command": "fixture-not-run"}
        )
    old_alpha = endpoint_identity(database, "service", "alpha")
    old_beta = endpoint_identity(database, "service", "beta")
    config = ConfigService.load_raw_config()
    del config["mcpServers"]["alpha"]
    assert ConfigService.save_config(config)
    assert current_endpoint_identity(database, "service", "alpha") is None
    assert endpoint_identity(database, "service", "beta") == old_beta
    assert ConfigService.add_server_to_config(
        "alpha", {"type": "stdio", "command": "fixture-not-run"}
    )
    assert endpoint_identity(database, "service", "alpha") != old_alpha


@pytest.mark.asyncio
async def test_source_extension_can_run_without_independent_skills_endpoint(
    content_app, monkeypatch
):
    _, database = content_app
    app = source_app(database, monkeypatch, independent=False)
    publish_bound(database)
    assert app.state.skills_mcp_runtime.http_app is not None
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            assert (
                len((await rpc(client, path="alpha")).json()["result"]["skills"]) == 1
            )
            assert (await rpc(client, path="skills")).json()["result"]["skills"] == []
