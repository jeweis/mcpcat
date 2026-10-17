"""真实应用工厂的独立 Skills 端点、Key/缓存/配置回归。"""

from dataclasses import replace
from hashlib import sha256

import httpx2
import pytest
from test_remote_skill_protocol import skill_package
from test_skill_content_workflow import READ, WRITE, Manager, content_app

from app.application import create_app
from app.models.skills_mcp_config import SkillsMCPConfig
from app.services.config_service import ConfigService
from app.services.inspector_service import inspector_service
from app.services.market_service import MarketService
from app.services.skill_profile_upload import upload_skill_profiles
from app.services.skill_resource_uri import parse_skill_resource_uri, skill_resource_uri
from app.services.skill_variant_publication import publish_content_version
from app.storage.unit_of_work import UnitOfWork


def configured_app(database, monkeypatch, **overrides):
    assert ConfigService.update_setting_section(
        "skills_mcp", {"enabled": True, **overrides}
    )
    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    return create_app(database=database, server_manager=Manager())


async def rpc(
    client,
    method="skills/list",
    params=None,
    *,
    key=READ,
    header="Mcpcat-Key",
    path="skills",
):
    headers = {
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": method,
        "Accept": "application/json, text/event-stream",
    }
    if key is not None:
        headers[header] = key
    if params and "uri" in params:
        headers["Mcp-Name"] = params["uri"]
    elif params and "name" in params:
        headers["Mcp-Name"] = params["name"]
    return await client.post(
        f"/mcp/{path}/",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": {
                **(params or {}),
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientInfo": {
                        "name": "fixture",
                        "version": "1",
                    },
                    "io.modelcontextprotocol/clientCapabilities": {
                        "extensions": {"io.modelcontextprotocol/skills": {}}
                    },
                },
            },
        },
    )


def publish(database, *, version="1.0.0", public=False, extras=None):
    upload_skill_profiles(
        database,
        content=skill_package(extras=extras),
        version=version,
        selection="remote",
    )
    publish_content_version(
        database, slug="demo", version=version, remote_public=public
    )


@pytest.mark.asyncio
async def test_uploaded_both_roundtrip_preview_zip_and_remote_never_executes(
    content_app, monkeypatch
):
    _, database = content_app
    app = configured_app(database, monkeypatch)
    marker = database.path.parent / "uploaded-script-must-not-run"
    script = f"open({str(marker)!r}, 'w').write('not allowed')\n".encode()
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            upload = await client.post(
                "/api/skill-variants/upload",
                headers={"Mcpcat-Key": WRITE},
                files={
                    "file": (
                        "demo.zip",
                        skill_package(extras={"scripts/run.py": script}),
                        "application/zip",
                    )
                },
                data={
                    "version": "1.0.0",
                    "selection": "both",
                    "binding_kind": "independent",
                },
            )
            assert upload.status_code == 200, upload.text
            summary = upload.json()
            for profile in ("local", "remote"):
                preview = await client.get(
                    "/api/skill-variants/demo/versions/1.0.0/file",
                    headers={"Mcpcat-Key": WRITE},
                    params={"profile": profile, "path": "demo/scripts/run.py"},
                )
                assert preview.json()["content"].encode() == script
            assert (await rpc(client)).json()["result"]["skills"] == []
            published = await client.post(
                "/api/skill-variants/demo/versions/1.0.0/publish",
                headers={"Mcpcat-Key": WRITE},
                json={},
            )
            assert published.status_code == 200
            local = await client.get(
                "/api/skills/demo/versions/1.0.0/download", headers={"Mcpcat-Key": READ}
            )
            remote = await client.get(
                "/api/skill-variants/demo/versions/1.0.0/download",
                headers={"Mcpcat-Key": READ},
                params={"profile": "remote"},
            )
            assert local.content == remote.content
            assert sha256(local.content).hexdigest() == summary["sha256"]
            entry = (await rpc(client)).json()["result"]["skills"][0]
            script_uri = skill_resource_uri(
                replace(parse_skill_resource_uri(entry["uri"]), file="scripts/run.py")
            )
            resource = (
                await rpc(client, "resources/read", {"uri": script_uri})
            ).json()["result"]["contents"][0]
            assert resource["text"].encode() == script
            assert (await rpc(client, "tools/list")).json()["result"]["tools"] == []
            assert not marker.exists()


@pytest.mark.asyncio
async def test_factory_mount_key_cache_and_live_disable(content_app, monkeypatch):
    _, database = content_app
    assert ConfigService.update_setting_section("catalog", {"enabled": False})
    app = configured_app(database, monkeypatch)
    assert app.state.server_manager.catalog_service is None
    marker = database.path.parent / "must-not-execute"
    script = f"open({str(marker)!r}, 'w').write('executed')\n".encode()
    publish(database, extras={"scripts/example.py": script})
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            unauthorized = await rpc(client, key=None)
            assert unauthorized.status_code == 401
            assert unauthorized.headers["cache-control"] == "private, no-store"
            assert "Mcpcat-Key" in unauthorized.headers["vary"]
            response = await rpc(client)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "private, no-store"
            entry = response.json()["result"]["skills"][0]
            assert (await rpc(client, "tools/list")).json()["result"]["tools"] == []
            script_uri = skill_resource_uri(
                replace(
                    parse_skill_resource_uri(entry["uri"]), file="scripts/example.py"
                )
            )
            downloaded = await rpc(client, "resources/read", {"uri": script_uri})
            assert downloaded.json()["result"]["contents"][0]["text"].encode() == script
            assert not marker.exists()
            assert (
                await rpc(client, "resources/read", {"uri": entry["uri"]})
            ).status_code == 200
            connection = await client.get(
                "/api/skill-variants/demo/connections", headers={"Mcpcat-Key": READ}
            )
            assert connection.json()["connections"][0]["ready"] is True
            assert (
                connection.json()["connections"][0]["endpoint_url"]
                == "http://fixture/mcp/skills"
            )
            upload_skill_profiles(
                database, content=skill_package(), version="1.0.1", selection="remote"
            )
            identity = parse_skill_resource_uri(entry["uri"])
            draft_uri = skill_resource_uri(replace(identity, version="1.0.1"))
            draft = await rpc(client, "skills/get", {"uri": draft_uri}, key=WRITE)
            assert draft.json()["error"]["code"] == -32602
            assert ConfigService.update_setting_section(
                "skills_mcp", {"enabled": False}
            )
            assert (await rpc(client, "server/discover")).status_code == 200
            collision = await client.post(
                "/api/servers",
                headers={"Mcpcat-Key": WRITE},
                json={
                    "name": "skills",
                    "config": {"type": "stdio", "command": "fixture-not-run"},
                },
            )
            assert collision.status_code == 409
            assert ConfigService.get_server_config("skills") is None
            assert (
                await client.get(
                    "/api/skill-variants/demo/connections", headers={"Mcpcat-Key": READ}
                )
            ).json()["connections"][0]["ready"] is True
    assert app.state.skills_mcp_runtime.alive is False


@pytest.mark.asyncio
async def test_public_endpoint_requires_publication_review_and_key_revocation(
    content_app, monkeypatch
):
    _, database = content_app
    app = configured_app(database, monkeypatch, require_auth=False)
    publish(database)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            assert (await rpc(client, key=None)).json()["result"]["skills"] == []
            connection = (
                await client.get(
                    "/api/skill-variants/demo/connections", headers={"Mcpcat-Key": READ}
                )
            ).json()["connections"][0]
            assert connection["ready"] is True
            assert connection["require_auth"] is False
            assert connection["public_readable"] is False
            private_uri = (await rpc(client)).json()["result"]["skills"][0]["uri"]
            assert (
                await rpc(client, "skills/get", {"uri": private_uri}, key=None)
            ).json()["error"]["code"] == -32602
            with UnitOfWork(database) as unit:
                unit.api_keys.update(READ, {"enabled": False})
                unit.commit()
            assert (await rpc(client)).json()["result"]["skills"] == []
            assert (await rpc(client, "resources/read", {"uri": private_uri})).json()[
                "error"
            ]["code"] == -32602
            publish(database, version="1.0.1", public=True)
            assert len((await rpc(client, key=None)).json()["result"]["skills"]) == 1
            connections = (
                await client.get(
                    "/api/skill-variants/demo/connections",
                    headers={"Mcpcat-Key": WRITE},
                )
            ).json()["connections"]
            assert {row["version"]: row["public_readable"] for row in connections} == {
                "1.0.0": False,
                "1.0.1": True,
            }


@pytest.mark.asyncio
async def test_custom_auth_header_path_and_identity_restart(content_app, monkeypatch):
    _, database = content_app
    assert ConfigService.update_setting_section(
        "security", {"auth_header_name": "X-Fixture-Key"}
    )
    app = configured_app(database, monkeypatch, path_name="workflows")
    publish(database)
    runtime = app.state.skills_mcp_runtime
    # 重启/地址变化不会替换持久身份，未启动的新实例不报告 ready。
    another = create_app(database=database, server_manager=Manager())
    assert another.state.skills_mcp_runtime.scope == runtime.scope
    assert not another.state.skills_mcp_runtime.available()
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            assert (await rpc(client, path="workflows")).status_code == 401
            response = await rpc(client, path="workflows", header="X-Fixture-Key")
            assert response.status_code == 200
            assert response.headers["vary"] == "X-Fixture-Key"
            assert ConfigService.update_setting_section(
                "skills_mcp", {"enabled": True, "path_name": "other"}
            )
            assert (
                await rpc(client, path="workflows", header="X-Fixture-Key")
            ).status_code == 404


@pytest.mark.parametrize("section", ["mcpServers", "catalog", "apps", "code_mode"])
def test_path_conflicts_fail_without_overwriting_resources(
    content_app, monkeypatch, section
):
    _, database = content_app
    if section == "mcpServers":
        assert ConfigService.add_server_to_config(
            "skills", {"type": "stdio", "command": "fixture-not-run"}
        )
    else:
        assert ConfigService.update_setting_section(
            section, {"enabled": True, "path_name": "skills"}
        )
    app = configured_app(database, monkeypatch)
    paths = [
        app.state.skills_mcp_runtime.config.path_name,
        app.state.apps_mcp_runtime.config.path_name,
        app.state.code_mode_runtime.config.path_name,
    ]
    assert len(set(paths)) == 3
    if section in {"mcpServers", "catalog"}:
        assert "skills" not in paths
    if section == "mcpServers":
        assert ConfigService.get_server_config("skills")["command"] == "fixture-not-run"


def test_disabled_upgrade_keeps_config_and_no_protocol_mount(content_app):
    app, database = content_app
    before = ConfigService.load_raw_config()
    another = create_app(database=database, server_manager=Manager())
    assert ConfigService.load_raw_config() == before
    assert another.state.skills_mcp_scopes[""]["path_name"] == "skills"
    assert another.state.skills_mcp_runtime.http_app is not None
    assert "enabled" not in SkillsMCPConfig().model_dump()


@pytest.mark.asyncio
async def test_unmounted_or_invalid_setting_does_not_make_user_server_public(
    content_app,
):
    app, _ = content_app
    assert ConfigService.add_server_to_config(
        "skills", {"type": "stdio", "command": "fixture-not-run", "require_auth": True}
    )
    for value in (
        {"enabled": "false", "require_auth": False},
        {"enabled": True, "require_auth": False},
    ):
        assert ConfigService.update_setting_section("skills_mcp", value)
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            assert (await rpc(client, key=None)).status_code == 401


@pytest.mark.asyncio
async def test_admin_settings_authority_partial_update_and_restart_state(content_app):
    app, _ = content_app
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        path = "/api/admin/settings/skills-mcp"
        assert (await client.get(path, headers={"Mcpcat-Key": READ})).status_code == 403
        assert (
            await client.put(path, headers={"Mcpcat-Key": READ}, json={"enabled": True})
        ).status_code == 403
        assert (
            "enabled"
            not in (await client.get(path, headers={"Mcpcat-Key": WRITE})).json()[
                "config"
            ]
        )
        enabled = await client.put(
            path,
            headers={"Mcpcat-Key": WRITE},
            json={
                "enabled": True,
                "path_name": "workflows",
                "future-field": {"retained": True},
            },
        )
        assert enabled.status_code == 200
        assert (
            enabled.json()["restart_required"] is True
            and enabled.json()["ready"] is False
        )
        disabled = await client.put(
            path, headers={"Mcpcat-Key": WRITE}, json={"enabled": False}
        )
        assert disabled.json()["config"]["path_name"] == "workflows"
        assert disabled.json()["config"]["future-field"] == {"retained": True}
        assert disabled.json()["restart_required"] is True
        before = ConfigService.get_setting_section("skills_mcp")
        invalid = await client.put(
            path, headers={"Mcpcat-Key": WRITE}, json={"path_name": "../other"}
        )
        assert invalid.status_code == 422
        assert ConfigService.get_setting_section("skills_mcp") == before
        conflict = await client.put(
            path,
            headers={"Mcpcat-Key": WRITE},
            json={"enabled": True, "path_name": "mcpcat"},
        )
        assert conflict.status_code == 409
        assert ConfigService.get_setting_section("skills_mcp") == before


@pytest.mark.asyncio
async def test_actual_keys_cannot_share_listing_cursors(content_app, monkeypatch):
    _, database = content_app
    app = configured_app(database, monkeypatch, page_size=1)
    publish(database)
    upload_skill_profiles(
        database, content=skill_package("another"), version="1.0.0", selection="remote"
    )
    publish_content_version(database, slug="another", version="1.0.0")
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            first = (await rpc(client)).json()["result"]
            cursor = first["nextCursor"]
            wrong = await rpc(client, params={"cursor": cursor}, key=WRITE)
            assert wrong.json()["error"]["code"] == -32602
            same = await rpc(client, params={"cursor": cursor})
            assert same.status_code == 200
            assert len(same.json()["result"]["skills"]) == 1
