"""注册/刷新异步结果不得覆盖新来源、新凭证或失败的持久化。"""

from copy import deepcopy
from types import SimpleNamespace

import httpx
import httpx2
import pytest
from test_oauth_callbacks import callback_app, token_client
from test_oauth_client_lifecycle import HTTPClient
from test_oauth_client_registration import no_client
from test_oauth_probe_manager import manager_fixture

from app.services import oauth_client_registration as registration
from app.services.config_service import ConfigService


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["replace", "url", "oauth", "save_failure"])
async def test_registration_not_saved_if_context_changed(
    callback_app, monkeypatch, change
):
    app, manager, info, saved = callback_app
    info["config"]["oauth"] = no_client().model_dump(exclude_none=True)
    original = deepcopy(info["config"]["oauth"])

    def handler(request):
        if change == "replace":
            manager.server_info["fixture"] = deepcopy(info)
        elif change == "url":
            info["config"]["url"] = "https://changed.fixture/mcp"
        elif change == "oauth":
            info["config"]["oauth"]["client_id"] = "manually-set-during-registration"
        else:
            monkeypatch.setattr(
                ConfigService, "update_server_oauth", lambda *args: False
            )
        return httpx2.Response(
            201, json={"client_id": "not-saved", "client_secret": "fixture-secret"}
        )

    real = httpx2.AsyncClient
    monkeypatch.setattr(
        registration,
        "httpx2",
        SimpleNamespace(
            AsyncClient=lambda **kwargs: real(
                **kwargs, transport=httpx2.MockTransport(handler)
            ),
            Request=httpx2.Request,
            Response=httpx2.Response,
        ),
    )
    async with HTTPClient(
        transport=httpx2.ASGITransport(app), base_url="https://gateway.fixture"
    ) as client:
        response = await client.post("/api/oauth/fixture/authorize")
    assert response.status_code == (500 if change == "save_failure" else 409)
    assert not saved
    assert (
        manager.server_info["fixture"]["config"]["oauth"].get("client_id")
        != "not-saved"
    )
    if change == "save_failure":
        assert info["config"]["oauth"] == original


@pytest.mark.asyncio
async def test_manual_new_client_does_not_inherit_old_secret_or_token(callback_app):
    app, manager, info, saved = callback_app
    info["config"]["oauth"].update(
        token={"access_token": "old"}, unknown={"preserve": True}
    )
    async with HTTPClient(
        transport=httpx2.ASGITransport(app), base_url="https://gateway.fixture"
    ) as client:
        response = await client.post(
            "/api/oauth/fixture/set-credentials",
            json={"client_id": "new-public-client"},
        )
    assert response.status_code == 200
    selected = info["config"]["oauth"]
    assert "client_secret" not in selected and "token" not in selected
    assert selected["token_endpoint_auth_method"] == "none"
    assert selected["credentials_issuer"] == selected["issuer"]
    assert selected["unknown"] == {"preserve": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["replace", "url", "oauth", "stopped", "save_failure"]
)
async def test_background_refresh_cannot_overwrite_new_state(monkeypatch, change):
    manager, value = manager_fixture()
    value["oauth"] = {
        "token_endpoint": "https://issuer.fixture/token",
        "client_id": "old-client",
        "token": {
            "access_token": "expired",
            "refresh_token": "fixture-refresh",
            "expires_at": "2000-01-01",
        },
        "unknown": {"preserve": True},
    }
    original, saved = deepcopy(value["oauth"]), []
    monkeypatch.setattr(
        ConfigService, "update_server_oauth", lambda *args: saved.append(args) or True
    )

    def handler(request):
        if change == "replace":
            manager.server_info["fixture"] = deepcopy(manager.server_info["fixture"])
        elif change == "url":
            value["url"] = "https://changed.fixture/mcp"
        elif change == "oauth":
            value["oauth"]["client_id"] = "changed-client"
        elif change == "stopped":
            manager.server_info["fixture"]["status"] = "stopped"
        else:
            monkeypatch.setattr(
                ConfigService, "update_server_oauth", lambda *args: False
            )
        return httpx.Response(200, json={"access_token": "not-saved"})

    token_client(monkeypatch, handler)
    await manager._detect_auth_status("fixture", value)
    assert not saved
    assert value["oauth"]["token"]["access_token"] == "expired"
    if change == "save_failure":
        assert manager.server_info["fixture"]["auth_status"] == "auth_expired"
        assert value["oauth"] == original


@pytest.mark.asyncio
async def test_valid_legacy_token_does_not_force_registration_or_mutate_data(
    monkeypatch,
):
    manager, value = manager_fixture()
    value["oauth"] = {
        "client_id": "legacy",
        "token": {"access_token": "valid"},
        "unknown": {"preserve": True},
    }
    original, calls = deepcopy(value), []
    token_client(monkeypatch, lambda request: calls.append(request))
    monkeypatch.setattr(
        ConfigService, "update_server_oauth", lambda *args: calls.append(args)
    )
    await manager._detect_auth_status("fixture", value)
    assert value == original and not calls
    assert manager.server_info["fixture"]["auth_status"] == "authorized"
