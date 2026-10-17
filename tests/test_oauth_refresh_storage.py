"""v1.0.1 字段形状的旧 token 经真实 SQLite Repository 刷新，未知配置不丢失。"""

from copy import deepcopy
from urllib.parse import parse_qs

import httpx
import pytest
from test_oauth_callbacks import token_client
from test_oauth_probe_manager import manager_fixture
from test_storage import database

from app.services.config_service import ConfigService
from app.storage.unit_of_work import UnitOfWork


def legacy_oauth():
    return {
        "client_id": "legacy-client",
        "client_secret": "legacy-fixture-secret",
        "token_endpoint": "https://issuer.fixture/token",
        "scopes": ["read"],
        "redirect_mode": "manual",
        "token": {
            "access_token": "expired",
            "refresh_token": "legacy-refresh",
            "expires_at": "2000-01-01",
            "token_type": "Bearer",
        },
        "future_oauth": {"preserved": [1, 2]},
    }


def token_handler(request):
    data = parse_qs(request.content.decode())
    assert data["client_id"] == ["legacy-client"]
    assert data["client_secret"] == ["legacy-fixture-secret"]
    assert data["refresh_token"] == ["legacy-refresh"]
    return httpx.Response(200, json={"access_token": "new-stored", "expires_in": 3600})


@pytest.mark.asyncio
async def test_v101_token_refresh_preserves_sqlite_config(database, monkeypatch):
    manager, value = manager_fixture()
    value["oauth"] = legacy_oauth()
    original = deepcopy(value)
    monkeypatch.setattr(ConfigService, "_database", database)
    with UnitOfWork(database) as unit:
        unit.mcp_servers.upsert("fixture", value)
        unit.commit()
    token_client(monkeypatch, token_handler)
    await manager._detect_auth_status("fixture", value)
    with UnitOfWork(database) as unit:
        stored = unit.mcp_servers.get_config("fixture")
    assert stored["unknown"] == original["unknown"]
    assert stored["oauth"]["future_oauth"] == original["oauth"]["future_oauth"]
    assert stored["oauth"]["client_id"] == "legacy-client"
    assert stored["oauth"]["client_secret"] == "legacy-fixture-secret"
    assert stored["oauth"]["token"]["access_token"] == "new-stored"
    assert stored["oauth"]["token"]["refresh_token"] == "legacy-refresh"
    assert "issuer" not in stored["oauth"]
    assert value["oauth"] == stored["oauth"]
