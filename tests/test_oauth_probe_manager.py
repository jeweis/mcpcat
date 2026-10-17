"""探测失败/迟到结果不伪造就绪，不覆盖新来源；原授权 REST 仍可用。"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import oauth
from app.models.mcp_config import AuthStatus
from app.services.config_service import ConfigService
from app.services.oauth_flow import OAuthFlowService
from app.services.oauth_probe import OAuthProbeError
from app.services.server_manager import MCPServerManager


def manager_fixture():
    manager = MCPServerManager()
    config = {
        "type": "streamable-http",
        "url": "https://resource.fixture/mcp",
        "headers": {"X-Upstream": "fixture"},
        "unknown": {"preserve": True},
    }
    manager.server_info["fixture"] = {
        "config": config,
        "status": "running",
        "auth_status": "none",
    }
    return manager, config


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category", ["timeout", "protocol_version", "connection", "http"]
)
async def test_probe_error_not_reported_ready(monkeypatch, category):
    manager, config = manager_fixture()

    async def inspect(url, headers, server_type):
        assert headers == config["headers"]
        raise OAuthProbeError(category)

    monkeypatch.setattr(
        OAuthFlowService, "inspect_auth_requirement", staticmethod(inspect)
    )
    await manager._detect_auth_status("fixture", config)
    assert manager.server_info["fixture"]["status"] == "failed"
    assert category in manager.server_info["fixture"]["error"]
    assert "oauth" not in config
    assert config["unknown"] == {"preserve": True}


@pytest.mark.asyncio
async def test_discovery_persists_issuer_metadata_without_second_probe(monkeypatch):
    manager, config = manager_fixture()
    saved = []
    metadata = {
        "issuer": "https://auth.fixture/tenant",
        "resource": config["url"],
        "authorization_endpoint": "https://auth.fixture/auth",
        "token_endpoint": "https://auth.fixture/token",
        "scopes_supported": ["read"],
        "client_id_metadata_document_supported": True,
    }

    async def inspect(*args):
        return AuthStatus.AUTH_REQUIRED, metadata

    monkeypatch.setattr(
        OAuthFlowService, "inspect_auth_requirement", staticmethod(inspect)
    )
    monkeypatch.setattr(
        ConfigService, "update_server_oauth", lambda *args: saved.append(args)
    )
    await manager._detect_auth_status("fixture", config)
    assert manager.server_info["fixture"]["auth_status"] == "auth_required"
    assert saved == [("fixture", config["oauth"])]
    assert config["oauth"]["issuer"] == metadata["issuer"]
    assert config["oauth"]["client_id_metadata_document_supported"] is True
    assert config["oauth"]["scopes"] == ["read"]
    assert config["oauth"]["token"] is None


@pytest.mark.asyncio
async def test_existing_manual_credentials_and_unknown_fields_preserved(monkeypatch):
    manager, config = manager_fixture()
    existing = {
        "client_id": "pre-registered",
        "client_secret": "fixture-secret",
        "redirect_mode": "auto",
        "scopes": ["custom"],
        "unknown": {"keep": True},
    }
    config["oauth"] = dict(existing)

    async def inspect(*args):
        return AuthStatus.AUTH_REQUIRED, {
            "issuer": "https://auth.fixture",
            "authorization_endpoint": "https://auth.fixture/authorize",
        }

    monkeypatch.setattr(
        OAuthFlowService, "inspect_auth_requirement", staticmethod(inspect)
    )
    monkeypatch.setattr(ConfigService, "update_server_oauth", lambda *args: True)
    await manager._detect_auth_status("fixture", config)
    for field, value in existing.items():
        assert config["oauth"][field] == value


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_stale_probe_does_not_mutate_replacement(monkeypatch, failed):
    manager, config = manager_fixture()
    started, finish = asyncio.Event(), asyncio.Event()
    saved = []

    async def inspect(*args):
        started.set()
        await finish.wait()
        if failed:
            raise OAuthProbeError("connection")
        return AuthStatus.AUTH_REQUIRED, {
            "authorization_endpoint": "https://auth.fixture/auth"
        }

    monkeypatch.setattr(
        OAuthFlowService, "inspect_auth_requirement", staticmethod(inspect)
    )
    monkeypatch.setattr(
        ConfigService, "update_server_oauth", lambda *args: saved.append(args)
    )
    task = asyncio.create_task(manager._detect_auth_status("fixture", config))
    await started.wait()
    replacement = {
        "config": {"url": "https://replacement.fixture/mcp"},
        "status": "running",
    }
    manager.server_info["fixture"] = replacement
    finish.set()
    await task
    assert manager.server_info["fixture"] == replacement
    assert "oauth" not in config
    assert not saved


@pytest.mark.asyncio
async def test_authorize_maps_probe_error_to_safe_502(monkeypatch):
    manager, config = manager_fixture()
    monkeypatch.setattr(oauth, "server_manager", manager)

    async def discover(*args):
        assert args == (config["url"], config["headers"], config["type"])
        raise OAuthProbeError("protocol_version")

    monkeypatch.setattr(OAuthFlowService, "discover_endpoints", staticmethod(discover))
    with pytest.raises(HTTPException) as raised:
        await oauth.authorize_server("fixture", SimpleNamespace())
    assert raised.value.status_code == 502
    assert "resource.fixture" not in raised.value.detail


@pytest.mark.asyncio
async def test_authorize_preserves_manual_credentials_flow(monkeypatch):
    manager, config = manager_fixture()
    monkeypatch.setattr(oauth, "server_manager", manager)
    monkeypatch.setattr(ConfigService, "get_public_base_url", lambda: None)
    saved = []
    monkeypatch.setattr(
        ConfigService, "update_server_oauth", lambda *args: saved.append(args) or True
    )

    async def discover(*args):
        return {
            "issuer": "https://auth.fixture",
            "authorization_endpoint": "https://auth.fixture/auth",
            "token_endpoint": "https://auth.fixture/token",
            "scopes_supported": ["read"],
        }

    monkeypatch.setattr(OAuthFlowService, "discover_endpoints", staticmethod(discover))
    result = await oauth.authorize_server(
        "fixture", SimpleNamespace(base_url="https://gateway.fixture/")
    )
    assert result["needs_credentials"] is True
    assert saved[0][1].model_dump()["issuer"] == "https://auth.fixture"
    assert config["unknown"] == {"preserve": True}
