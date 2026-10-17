"""默认关闭/有限配置/路径和管理员权限，未挂载不得报告就绪。"""

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from test_skill_content_workflow import READ, WRITE, Manager, content_app

from app.application import create_app
from app.models.code_mode_config import CodeModeConfig
from app.services.code_mode_settings import code_mode_config
from app.services.config_service import ConfigService


def test_missing_code_mode_defaults_do_not_write_old_config(content_app):
    before = ConfigService.load_raw_config()
    config = code_mode_config()
    assert "enabled" not in config.model_dump()
    assert config.path_name == "code-mode"
    assert config.allowed_services is None
    assert config.allowed_tools is None
    assert config.limits.monty_limits() == {
        "max_feed_duration_secs": 30.0,
        "max_memory": 100_000_000,
    }
    assert ConfigService.load_raw_config() == before


@pytest.mark.parametrize(
    "payload",
    [
        {"require_auth": False},
        {"limits": None},
        {"limits": {"max_tool_calls": None}},
        {"limits": {"memory_bytes": 0}},
        {"limits": {"tool_concurrency": True}},
        {"limits": {"deadline_seconds": float("inf")}},
        {"limits": {"sandbox_seconds": float("nan")}},
        {"limits": {"unsupported": 1}},
        {"allowed_tools": [" "]},
    ],
)
def test_code_mode_configuration_rejects_unbounded_or_ambiguous_values(payload):
    with pytest.raises(ValidationError):
        CodeModeConfig.model_validate(payload)


@pytest.mark.asyncio
async def test_admin_partial_settings_are_safe_and_not_fake_ready(content_app):
    app, _ = content_app
    assert ConfigService.update_setting_section(
        "code_mode",
        {
            "enabled": False,
            "unknown": {"keep": True},
            "future_credentials": "must-not-return-credential",
            "limits": {"memory_bytes": 200_000_000},
        },
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fixture",
    ) as client:
        assert (
            await client.get(
                "/api/admin/settings/code-mode", headers={"Mcpcat-Key": READ}
            )
        ).status_code == 403
        response = await client.put(
            "/api/admin/settings/code-mode",
            headers={"Mcpcat-Key": WRITE},
            json={
                "enabled": True,
                "allowed_services": [],
                "limits": {"max_tool_calls": 3},
            },
        )
        assert response.status_code == 200, response.text
        state = response.json()
        assert state["ready"] is False
        assert state["reason"] == "endpoint_not_mounted"
        assert state["restart_required"] is True
        assert state["config"]["allowed_services"] == []
        assert "unknown" not in state["config"]
        assert "must-not-return-credential" not in response.text
        stored = ConfigService.get_setting_section("code_mode", {})
        assert stored["unknown"] == {"keep": True}
        assert stored["future_credentials"] == "must-not-return-credential"
        assert state["config"]["limits"]["memory_bytes"] == 200_000_000
        assert state["config"]["limits"]["max_tool_calls"] == 3
        assert state["dependency_versions"] == {
            "fastmcp": "4.1.0",
            "pydantic-monty": "1.1.0",
        }
        old = ConfigService.load_raw_config()
        bad = await client.put(
            "/api/admin/settings/code-mode",
            headers={"Mcpcat-Key": WRITE},
            json={"limits": {"tool_timeout_seconds": 0}},
        )
        assert bad.status_code == 422
        assert ConfigService.load_raw_config() == old


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["mcpServers", "catalog", "skills_mcp", "apps"])
async def test_code_mode_conflicts_do_not_overwrite_configuration(content_app, section):
    app, _ = content_app
    if section == "mcpServers":
        ConfigService.add_server_to_config(
            "occupied", {"type": "stdio", "command": "not-run"}
        )
    else:
        ConfigService.update_setting_section(
            section, {"enabled": True, "path_name": "occupied"}
        )
    before = ConfigService.load_raw_config()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fixture",
        headers={"Mcpcat-Key": WRITE},
    ) as client:
        response = await client.put(
            "/api/admin/settings/code-mode",
            json={"enabled": True, "path_name": "occupied"},
        )
    assert response.status_code == 409
    assert ConfigService.load_raw_config() == before


@pytest.mark.asyncio
async def test_stale_route_and_reserved_pending_path_are_not_overwritten(content_app):
    app, _ = content_app
    app.mount("/mcp/stale", FastAPI())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fixture",
        headers={"Mcpcat-Key": WRITE},
    ) as client:
        assert (
            await client.put(
                "/api/admin/settings/code-mode",
                json={"enabled": True, "path_name": "stale"},
            )
        ).status_code == 409
        assert (
            await client.put("/api/admin/settings/code-mode", json={"enabled": True})
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
        assert ConfigService.get_server_config("code-mode") is None


def test_boot_refuses_conflicting_code_path_before_loading_servers(content_app):
    _, database = content_app
    ConfigService.update_setting_section(
        "code_mode", {"enabled": True, "path_name": "skills"}
    )
    ConfigService.update_setting_section("skills_mcp", {"enabled": True})
    manager = Manager()
    app = create_app(database=database, server_manager=manager)
    assert app.state.code_mode_runtime.config.path_name == "skills"
    assert app.state.skills_mcp_runtime.config.path_name == "mcpcat-skills"


@pytest.mark.asyncio
async def test_dependency_drift_refuses_enable_without_installing(
    content_app, monkeypatch
):
    app, _ = content_app
    monkeypatch.setattr(
        "app.services.code_mode_settings.code_mode_dependency_versions",
        lambda: {"fastmcp": "4.1.0", "pydantic-monty": None},
    )
    before = ConfigService.load_raw_config()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fixture",
        headers={"Mcpcat-Key": WRITE},
    ) as client:
        response = await client.put(
            "/api/admin/settings/code-mode", json={"enabled": True}
        )
    assert response.status_code == 409
    assert ConfigService.load_raw_config() == before
