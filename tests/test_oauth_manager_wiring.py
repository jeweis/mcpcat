# OAuth 授权/状态接口必须使用 app.state 里实际挂载的管理器，
# 而不是 create_app() 之外那个未加载任何服务器的模块级单例。
from types import SimpleNamespace

import httpx2
import pytest
from fastapi import FastAPI

from app.api import oauth
from app.services.config_service import ConfigService


def make_app(manager):
    app = FastAPI()
    app.state.server_manager = manager
    app.include_router(oauth.router, prefix="/api")
    return app


def fixture_manager(name, oauth_cfg=None):
    config = {"type": "streamable-http", "url": "https://resource.fixture/mcp"}
    if oauth_cfg is not None:
        config["oauth"] = oauth_cfg
    return SimpleNamespace(
        server_info={
            name: {
                "config": config,
                "status": "running",
                "auth_status": "auth_required",
            }
        },
        _update_server_status=lambda _n, _s: None,
    )


@pytest.mark.asyncio
async def test_status_uses_app_manager_not_module_singleton(monkeypatch):
    # 模块级单例故意不含该服务器；接口仍应能从 app.state 找到它（否则会 404）。
    monkeypatch.setattr(oauth, "server_manager", SimpleNamespace(server_info={}))
    app = make_app(fixture_manager("fixture"))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        response = await client.get("/api/oauth/fixture/status")
    assert response.status_code == 200, response.text
    assert response.json()["auth_status"] == "auth_required"


@pytest.mark.asyncio
async def test_authorize_route_reachable_with_app_manager(monkeypatch):
    # 该按钮此前 404：authorize 用的是空单例。修复后应从 app.state 找到服务器。
    monkeypatch.setattr(oauth, "server_manager", SimpleNamespace(server_info={}))
    monkeypatch.setattr(
        ConfigService, "get_public_base_url", lambda: "https://gateway.fixture"
    )
    monkeypatch.setattr(ConfigService, "update_server_oauth", lambda *a, **k: True)
    manager = fixture_manager(
        "fixture",
        {
            "issuer": "https://auth.fixture",
            "credentials_issuer": "https://auth.fixture",
            "authorization_endpoint": "https://auth.fixture/authorize",
            "token_endpoint": "https://auth.fixture/token",
            "client_id": "pre-registered-client",
            "code_challenge_methods_supported": ["S256"],
            "scopes": [],
        },
    )
    app = make_app(manager)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        response = await client.post("/api/oauth/fixture/authorize")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("authorize_url") or body.get("needs_credentials")
