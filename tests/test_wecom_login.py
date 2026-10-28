"""真实 HTTP 登录流程覆盖配置、CSRF、重放、账号生命周期与管理员权限。"""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.api import auth, wecom
from app.core.config import settings
from app.middleware.auth import AuthMiddleware
from app.models.mcp_config import PermissionType
from app.services.config_service import ConfigService
from app.services.security_service import SecurityService
from app.services.wecom_account_service import WeComAccountService
from app.services.wecom_auth_service import WeComAPIError, WeComUserInfo
from app.storage.database import Database
from app.storage.migrations import upgrade_schema
from app.storage.wecom_transactions import WeComLoginTransaction, WeComTransactions


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "mcpcat_backups_path", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "mcpcat_secret_key", "fixture-encryption-key")
    database = Database(tmp_path / "test.db")
    upgrade_schema(database)
    ConfigService.configure_database(database)
    ConfigService.save_config(ConfigService._create_default_config())
    security = SecurityService()
    service = WeComAccountService(security)
    monkeypatch.setattr(wecom, "wecom_account_service", service)
    monkeypatch.setattr(auth, "security_service", security)
    monkeypatch.setattr("app.middleware.auth.security_service", security)
    security.add_api_key(
        name="Admin", permission=PermissionType.WRITE, key="admin-fixture-key"
    )
    security.add_api_key(
        name="Reader", permission=PermissionType.READ, key="reader-fixture-key"
    )
    config = service.update_settings(
        {
            "enabled": True,
            "corp_id": "corp-fixture",
            "agent_id": "1000001",
            "app_secret": "secret-fixture",
            "redirect_uri": "https://gateway.fixture/api/auth/wecom/callback",
        }
    )
    calls = []

    def exchange(current, code):
        calls.append((current.corp_id, code))
        if code == "invalid":
            raise WeComAPIError("WECOM_API_ERROR", "授权码无效", 40029)
        if code == "external":
            raise WeComAPIError("WECOM_MEMBER_REQUIRED", "仅允许企业成员")
        return WeComUserInfo("user-fixture", "成员", "https://avatar.fixture/a.png")

    monkeypatch.setattr(wecom.wecom_auth_service, "exchange_code", exchange)
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(wecom.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    with TestClient(
        app, base_url="https://gateway.fixture", follow_redirects=False
    ) as client:
        yield client, service, config, database, calls
    ConfigService.reset_database()
    database.dispose()


def begin(client):
    response = client.get("/api/auth/wecom/authorize-url")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    url = urlsplit(response.json()["authorize_url"])
    assert url.netloc == "login.work.weixin.qq.com"
    params = parse_qs(url.query)
    assert params["login_type"] == ["CorpApp"]
    assert params["appid"] == ["corp-fixture"]
    assert params["agentid"] == ["1000001"]
    assert params["redirect_uri"] == ["https://gateway.fixture/api/auth/wecom/callback"]
    return params["state"][0]


def complete(client, code="valid", state=None):
    state = state or begin(client)
    response = client.get(
        "/api/auth/wecom/callback", params={"state": state, "code": code}
    )
    assert response.status_code == 303
    assert response.headers["referrer-policy"] == "no-referrer"
    location = urlsplit(response.headers["location"])
    assert location.netloc == "gateway.fixture"
    assert location.path == "/ui/"
    return parse_qs(urlsplit(location.fragment).query)


def logged_in(environment):
    client = environment[0]
    ticket = complete(client)["ticket"][0]
    response = client.post("/api/auth/wecom/login", json={"ticket": ticket})
    assert response.status_code == 200
    return response.json()


def test_complete_login_reuses_key_permissions_and_member_profile(environment):
    client, service, config, database, calls = environment
    assert client.get("/api/auth/wecom/status").json() == {"enabled": True}
    first = logged_in(environment)
    assert first["first_login"] and first["user"]["permission"] == "read"
    assert (
        client.get("/api/auth/info", headers={"Mcpcat-Key": first["token"]}).status_code
        == 200
    )
    member = service.list_members()[0]
    service.update_member(member["id"], {"permission": "write"})
    second = logged_in(environment)
    assert second["token"] == first["token"]
    assert not second["first_login"] and second["user"]["permission"] == "write"
    assert len(service.list_members()) == 1
    assert len(calls) == 2
    with database.session_scope() as session:
        rows = session.scalars(select(WeComLoginTransaction)).all()
        assert not rows


def test_state_cookie_binding_and_replay(environment):
    client, service, config, database, calls = environment
    state = begin(client)
    browser = client.cookies.get(wecom.COOKIE_NAME)
    client.cookies.clear()
    assert complete(client, state=state)["error"] == ["WECOM_STATE_INVALID"]
    assert calls == []
    client.cookies.set(wecom.COOKIE_NAME, browser)
    ticket = complete(client, state=state)["ticket"][0]
    assert complete(client, state=state)["error"] == ["WECOM_STATE_INVALID"]
    assert len(calls) == 1
    client.cookies.clear()
    assert (
        client.post("/api/auth/wecom/login", json={"ticket": ticket}).status_code == 400
    )
    client.cookies.set(wecom.COOKIE_NAME, browser)
    assert (
        client.post("/api/auth/wecom/login", json={"ticket": ticket}).status_code == 200
    )
    assert (
        client.post("/api/auth/wecom/login", json={"ticket": ticket}).status_code == 400
    )


@pytest.mark.parametrize("kind", ["state", "ticket"])
def test_expired_transactions_are_rejected(environment, kind):
    client, service, config, database, calls = environment
    if kind == "state":
        token = begin(client)
    else:
        token = complete(client)["ticket"][0]
    with database.session_scope() as session:
        session.execute(
            update(WeComLoginTransaction).values(expires_at=time.time() - 1)
        )
    if kind == "state":
        assert complete(client, state=token)["error"] == ["WECOM_STATE_INVALID"]
        assert calls == []
    else:
        assert (
            client.post("/api/auth/wecom/login", json={"ticket": token}).status_code
            == 400
        )


@pytest.mark.parametrize("stage", ["state", "ticket"])
def test_configuration_change_invalidates_pending_login(environment, stage):
    client, service, config, database, calls = environment
    token = begin(client) if stage == "state" else complete(client)["ticket"][0]
    service.update_settings({"app_secret": "new-secret"})
    if stage == "state":
        assert complete(client, state=token)["error"] == ["WECOM_STATE_INVALID"]
        assert calls == []
    else:
        assert (
            client.post("/api/auth/wecom/login", json={"ticket": token}).status_code
            == 400
        )


@pytest.mark.parametrize(
    "code,expected",
    [
        ("invalid", "WECOM_API_ERROR"),
        ("external", "WECOM_MEMBER_REQUIRED"),
        ("", "WECOM_CODE_REQUIRED"),
    ],
)
def test_failed_exchange_does_not_create_member(environment, code, expected):
    client, service, *_ = environment
    assert complete(client, code=code)["error"] == [expected]
    assert service.list_members() == []


@pytest.mark.parametrize("stage", ["callback", "redeem"])
@pytest.mark.parametrize("change", ["disabled", "expired", "deleted"])
def test_unavailable_members_cannot_login(environment, stage, change):
    client, service, config, database, calls = environment
    first = logged_in(environment)
    member = service.list_members()[0]
    pending = complete(client)["ticket"][0] if stage == "redeem" else None
    if change == "disabled":
        service.update_member(member["id"], {"enabled": False})
    elif change == "expired":
        assert service.security.update_api_key(
            first["token"], expires_at=datetime.now(timezone.utc) - timedelta(hours=1)
        )
    else:
        service.security.remove_api_key(first["token"])
    if stage == "redeem":
        assert (
            client.post("/api/auth/wecom/login", json={"ticket": pending}).status_code
            == 403
        )
    elif change != "deleted":
        assert complete(client)["error"] == ["WECOM_ACCOUNT_UNAVAILABLE"]
    # 显式删除后再次首次登录会按当前规则重新注册，与飞书行为一致。
    assert service.security.verify_api_key(first["token"]) is None


def test_settings_encrypted_masked_partial_and_write_permission(environment):
    client, service, *_ = environment
    path = "/api/admin/wecom/settings"
    assert client.get(path).status_code == 401
    reader = {"Mcpcat-Key": "reader-fixture-key"}
    assert client.get(path, headers=reader).status_code == 403
    assert client.put(path, headers=reader, json={"enabled": False}).status_code == 403
    admin = {"Mcpcat-Key": "admin-fixture-key"}
    response = client.get(path, headers=admin)
    assert response.json()["has_app_secret"]
    assert "secret-fixture" not in response.text and "app_secret" not in response.json()
    stored = ConfigService.get_setting_section("wecom")
    assert stored["app_secret"] and stored["app_secret"] != "secret-fixture"
    response = client.put(
        path, headers=admin, json={"default_permission": "write", "app_secret": " "}
    )
    assert response.status_code == 200
    assert service.config().app_secret == "secret-fixture"
    response = client.put(path, headers=admin, json={"enabled": False})
    assert response.status_code == 200
    assert client.get("/api/auth/wecom/status").json() == {"enabled": False}
    assert client.get("/api/auth/wecom/authorize-url").status_code == 400


@pytest.mark.parametrize(
    "updates",
    [
        {"agent_id": "abc"},
        {"agent_id": "0"},
        {"agent_id": ""},
        {"corp_id": ""},
        {"redirect_uri": "https://evil.fixture/#/callback"},
        {"redirect_uri": "http://gateway.fixture/api/auth/wecom/callback"},
        {"redirect_uri": "https://user:pass@gateway.fixture/api/auth/wecom/callback"},
        {"redirect_uri": "https://gateway.fixture/api/auth/wecom/callback?next=evil"},
        {"default_permission": "admin"},
    ],
)
def test_invalid_settings_do_not_overwrite_saved_settings(environment, updates):
    client, service, *_ = environment
    before = ConfigService.get_setting_section("wecom")
    response = client.put(
        "/api/admin/wecom/settings",
        json=updates,
        headers={"Mcpcat-Key": "admin-fixture-key"},
    )
    assert response.status_code in {400, 422}
    assert ConfigService.get_setting_section("wecom") == before


def test_members_admin_mutation_and_provider_isolation(environment):
    client, service, *_ = environment
    logged_in(environment)
    path = "/api/admin/wecom/users"
    reader = {"Mcpcat-Key": "reader-fixture-key"}
    admin = {"Mcpcat-Key": "admin-fixture-key"}
    assert client.get(path).status_code == 401
    assert client.get(path, headers=reader).status_code == 403
    response = client.get(path, headers=admin)
    member = response.json()[0]
    assert "key" not in member
    for updates in ({"permission": "write"}, {"enabled": False}):
        assert (
            client.put(
                f"{path}/{member['id']}", headers=reader, json=updates
            ).status_code
            == 403
        )
        response = client.put(f"{path}/{member['id']}", headers=admin, json=updates)
        assert response.status_code == 200
        assert all(response.json()[key] == value for key, value in updates.items())
        assert "key" not in response.json()
    assert (
        client.put(f"{path}/1", headers=admin, json={"enabled": False}).status_code
        == 404
    )
    assert service.security.verify_api_key("admin-fixture-key")
    assert (
        client.put(f"{path}/99999", headers=admin, json={"enabled": False}).status_code
        == 404
    )
    assert (
        client.put(
            f"{path}/{member['id']}", headers=admin, json={"permission": "admin"}
        ).status_code
        == 422
    )


def test_identity_is_unique_per_corporation_and_concurrent_first_login(environment):
    client, service, config, database, calls = environment
    user = WeComUserInfo("same-user", "Same User")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: service.find_or_create(config, user), range(4))
        )
    assert len({key.key for key, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    other_config = config.model_copy(update={"corp_id": "other-corp"})
    other, created = service.find_or_create(other_config, user)
    assert created and other.key != results[0][0].key
    assert len(service.list_members()) == 2


def test_transaction_atomic_consume(environment):
    database = environment[3]
    transactions = WeComTransactions(database)
    token = transactions.issue("ticket", "browser", {"id": 1}, 60)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: transactions.consume("ticket", token, "browser"), range(4)
            )
        )
    assert results.count({"id": 1}) == 1
    assert results.count(None) == 3


def test_old_ticket_cannot_login_to_deleted_and_recreated_account(environment):
    client, service, config, *_ = environment
    first = logged_in(environment)
    ticket = complete(client)["ticket"][0]
    service.security.remove_api_key(first["token"])
    replacement, created = service.find_or_create(config, WeComUserInfo("user-fixture"))
    assert created and replacement.key != first["token"]
    assert (
        client.post("/api/auth/wecom/login", json={"ticket": ticket}).status_code == 403
    )


def test_feishu_login_and_permission_management_are_compatible(
    environment, monkeypatch
):
    client, service, *_ = environment
    security = service.security
    security.update_feishu_settings(
        enabled=True, app_id="feishu-app", app_secret="feishu-secret"
    )
    monkeypatch.setattr(
        auth.feishu_auth_service, "exchange_code_v2", lambda **kwargs: "token"
    )
    monkeypatch.setattr(
        auth.feishu_auth_service,
        "get_user_info",
        lambda **kwargs: type(
            "User",
            (),
            {
                "union_id": "union",
                "open_id": "open",
                "name": "Feishu",
                "avatar_url": None,
            },
        )(),
    )
    assert client.get("/api/auth/feishu/status").json()["enabled"]
    first = client.post("/api/auth/feishu/login", json={"code": "code"}).json()
    assert first["first_login"] and first["user"]["permission"] == "read"
    admin = {"Mcpcat-Key": "admin-fixture-key"}
    assert (
        client.put(
            "/api/admin/feishu/users/union", headers=admin, json={"permission": "write"}
        ).status_code
        == 200
    )
    again = client.post("/api/auth/feishu/login", json={"code": "code"}).json()
    assert again["token"] == first["token"] and again["user"]["permission"] == "write"
    assert service.list_members() == []
