"""无平台开关的默认部署、旧输入、权限和路径避让回归。"""

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from test_skill_content_workflow import READ, WRITE, Manager, content_app
from test_skills_mcp_gateway import rpc

from app.application import create_app
from app.core.config import Settings
from app.services.config_service import ConfigService
from app.services.inspector_service import inspector_service
from app.services.market_service import MarketService
from app.services.platform_endpoints import ENDPOINT_MODELS, prepare_platform_paths


@pytest.mark.parametrize("legacy", [None, False, True, "false"])
async def test_default_and_legacy_switches_keep_all_capabilities(
    content_app, monkeypatch, legacy
):
    """旧 false 不关能力，不改原配置或放开匿名访问、资源发布。"""
    _, database = content_app
    monkeypatch.setenv("MCPCAT_SKILLS_ENABLED", "false")
    monkeypatch.setenv("MCPCAT_APPS_ENABLED", "false")
    assert "mcpcat_apps_enabled" not in Settings().model_dump()
    if legacy is not None:
        for section in ENDPOINT_MODELS:
            ConfigService.update_setting_section(
                section,
                {
                    "enabled": legacy,
                    "independent_enabled": False,
                    "future": {"keep": "unchanged"},
                },
            )
    before = ConfigService.load_raw_config()
    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    app = create_app(database=database, server_manager=Manager())
    assert ConfigService.load_raw_config() == before
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            for route in ["skills-mcp", "apps-mcp", "code-mode"]:
                response = await client.get(
                    f"/api/admin/settings/{route}", headers={"Mcpcat-Key": WRITE}
                )
                assert response.status_code == 200
                assert response.json()["ready"]
                assert "enabled" not in response.json()["config"]
                assert "independent_enabled" not in response.json()["config"]
            for path, method in [
                ("skills", "skills/list"),
                ("apps", "tools/list"),
                ("code-mode", "tools/list"),
            ]:
                assert (
                    await rpc(client, method, path=path, key=None)
                ).status_code == 401
                response = await rpc(client, method, path=path)
                assert response.status_code == 200 and "result" in response.json()
            assert (await rpc(client)).json()["result"]["skills"] == []
            assert (await rpc(client, "tools/list", path="apps")).json()["result"][
                "tools"
            ] == []
            assert (
                await client.get("/api/apps", headers={"Mcpcat-Key": READ})
            ).status_code == 200
            assert (
                await client.get("/api/skills/registry", headers={"Mcpcat-Key": READ})
            ).status_code == 200
            assert (
                await client.post("/api/apps", headers={"Mcpcat-Key": READ})
            ).status_code == 403


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_default_code_mode_native_client_and_sandbox(
    content_app, monkeypatch, mode
):
    """普通客户端无需配置开关；计算可用且宿主文件访问仍拒绝。"""
    app, _ = content_app
    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    transport = StreamableHttpTransport(
        "http://fixture/mcp/code-mode/",
        headers={"Mcpcat-Key": READ},
        httpx_client_factory=lambda **kwargs: httpx2.AsyncClient(
            **kwargs, transport=httpx2.ASGITransport(app=app)
        ),
    )
    async with app.router.lifespan_context(app), Client(transport, mode=mode) as client:
        assert {tool.name for tool in await client.list_tools()} == {
            "search",
            "get_schema",
            "execute",
        }
        assert not (
            await client.call_tool("execute", {"code": "return 1 + 2"})
        ).is_error
        result = await client.call_tool(
            "execute",
            {"code": 'return open("/etc/passwd").read()'},
            raise_on_error=False,
        )
        assert result.is_error


def test_occupied_old_routes_are_preserved_and_allocations_stable(content_app):
    """升级无须重命名用户服务；重复启动保留选择和未知配置。"""
    _, database = content_app
    for name in ["skills", "apps", "code-mode", "mcpcat-skills"]:
        ConfigService.add_server_to_config(
            name, {"type": "stdio", "command": "not-run"}
        )
    for section in ENDPOINT_MODELS:
        ConfigService.update_setting_section(
            section, {"enabled": False, "unknown": {"keep": True}}
        )
    servers = ConfigService.load_mcp_servers_config()
    prepare_platform_paths(Manager())
    expected = {
        "skills_mcp": "mcpcat-skills-2",
        "apps": "mcpcat-apps",
        "code_mode": "mcpcat-code-mode",
    }
    for section, path in expected.items():
        assert ConfigService.get_setting_section(section) == {
            "enabled": False,
            "unknown": {"keep": True},
            "path_name": path,
        }
    before = ConfigService.load_raw_config()
    create_app(database=database, server_manager=Manager())
    assert ConfigService.load_raw_config() == before
    assert ConfigService.load_mcp_servers_config() == servers


@pytest.mark.parametrize("section", list(ENDPOINT_MODELS))
def test_legacy_switches_never_override_resource_security(section):
    """废弃字段不进入新 Schema，其他权限和校验不被忽略。"""
    model = ENDPOINT_MODELS[section]
    config = model.model_validate({"enabled": False, "independent_enabled": False})
    assert config.require_auth
    assert "enabled" not in model.model_json_schema()["properties"]
    with pytest.raises(ValueError):
        model.model_validate({"enabled": False, "path_name": "../invalid"})


def test_saved_system_path_wins_over_new_defaults(content_app):
    """已有 Apps 自定义地址不被新增的默认 Skills 入口抢占。"""
    ConfigService.update_setting_section("apps", {"path_name": "skills"})
    prepare_platform_paths(Manager())
    assert ConfigService.get_setting_section("apps") == {"path_name": "skills"}
    assert (
        ConfigService.get_setting_section("skills_mcp")["path_name"] == "mcpcat-skills"
    )


async def test_pending_skills_path_is_reserved_before_restart(content_app):
    """新路径保存到重启之间，不允许创建同名用户服务。"""
    app, _ = content_app
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://fixture",
        headers={"Mcpcat-Key": WRITE},
    ) as client:
        response = await client.put(
            "/api/admin/settings/skills-mcp", json={"path_name": "workflow-files"}
        )
        assert response.status_code == 200 and response.json()["restart_required"]
        response = await client.post(
            "/api/servers",
            json={
                "name": "workflow-files",
                "config": {"type": "stdio", "command": "not-run"},
            },
        )
        assert response.status_code == 409
        assert ConfigService.get_server_config("workflow-files") is None
