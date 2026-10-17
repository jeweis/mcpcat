"""配置查询保持可读，但只读身份不得取得上游 OAuth 凭证。"""

from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from app.services.config_service import ConfigService
from app.services.server_config_visibility import reader_config
from app.services.server_manager import MCPServerManager
from tests.test_skill_content_workflow import READ, WRITE, content_app


@pytest.mark.asyncio
@pytest.mark.parametrize("grant", [None, "client_credentials"])
@pytest.mark.parametrize("kind", ["streamable-http", "sse", "stdio", "openapi"])
async def test_oauth_config_visible_only_to_writer(
    content_app, monkeypatch, grant, kind
):
    """经真实认证中间件测试旧 OAuth 和机器认证，不改变数据库原配置。"""
    app, _ = content_app
    config = {
        "type": kind,
        "url": "https://upstream.fixture/mcp?key=synthetic-url-secret",
        "api_base_url": "https://synthetic-user:synthetic-password@api.fixture",
        "spec_url": "https://api.fixture/spec?token=synthetic-spec-secret",
        "note": "Public description",
        "tags": ["demo"],
        "headers": {"X-Custom": "synthetic-header-secret"},
        "env": {"CUSTOM_VARIABLE": "synthetic-env-secret"},
        "command": "synthetic-shell-command",
        "args": ["--password", "synthetic-argument-secret"],
        "extension": {"opaque": "synthetic-extension-secret"},
        "oauth": {
            "client_secret": "synthetic-secret",
            "token": {
                "access_token": "synthetic-access",
                "refresh_token": "synthetic-refresh",
            },
            "private_key_env": "SYNTHETIC_PRIVATE_KEY",
            "future_credentials": {"secret": "synthetic-future"},
        },
    }
    if grant:
        config["oauth"]["grant_type"] = grant
    before = deepcopy(config)
    app.state.server_manager = SimpleNamespace(
        get_server_status=lambda: {"fixture": {"status": "running"}}
    )
    monkeypatch.setattr(ConfigService, "get_server_config", lambda _: config)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        reader = await client.get(
            "/api/servers/fixture/config", headers={"Mcpcat-Key": READ}
        )
        writer = await client.get(
            "/api/servers/fixture/config", headers={"Mcpcat-Key": WRITE}
        )
        anonymous = await client.get("/api/servers/fixture/config")
    assert reader.status_code == writer.status_code == 200
    assert anonymous.status_code == 401
    assert (
        "synthetic-" not in reader.text and "SYNTHETIC_PRIVATE_KEY" not in reader.text
    )
    assert reader.json()["config"]["oauth"] is None
    assert set(reader.json()["config"]) == {"type", "note", "tags", "oauth"}
    assert reader.json()["config"]["note"] == "Public description"
    assert writer.json()["config"] == before
    assert config == before


def test_public_projection_is_detached_and_keeps_absent_oauth_absent() -> None:
    """没有 OAuth 的旧配置不补字段，公开列表也不共享可修改引用。"""
    config = {"type": "stdio", "tags": ["public"], "env": {"SECRET": "fixture"}}
    visible = reader_config(config)
    assert visible == {"type": "stdio", "tags": ["public"]}
    visible["tags"].append("changed")
    assert config["tags"] == ["public"]


def test_status_exposes_machine_mode_without_credentials() -> None:
    """卡片能区分机器身份，但服务列表不会得到 OAuth 容器或秘密。"""
    manager = MCPServerManager.__new__(MCPServerManager)
    manager.server_info = {"fixture": {"config": {
        "type": "streamable-http", "oauth": {
            "grant_type": "client_credentials", "client_secret": "synthetic-secret",
            "private_key_env": "SYNTHETIC_KEY", "token": {"access_token": "synthetic-token"},
        },
    }}}
    status = manager.get_server_status()["fixture"]
    assert status["auth_mode"] == "client_credentials"
    assert "oauth" not in status and "synthetic" not in str(status)
    manager.server_info["fixture"]["config"]["oauth"].pop("grant_type")
    assert "auth_mode" not in manager.get_server_status()["fixture"]
