"""企微 API 适配验证：官方端点、凭证缓存、错误脱敏与成员身份边界。"""

from types import SimpleNamespace

import pytest
import requests

from app.models.wecom import WeComConfig
from app.services.wecom_auth_service import WeComAPIError, WeComAuthService


@pytest.fixture
def config():
    return WeComConfig(corp_id="corp", agent_id="10001", app_secret="fixture-secret")


def fake_requests(monkeypatch, replies):
    seen = []

    def get(url, **kwargs):
        seen.append((url, kwargs))
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(status_code=200, json=lambda: reply)

    monkeypatch.setattr(requests, "get", get)
    return seen


def test_exchange_member_token_cache_and_optional_profile(monkeypatch, config):
    replies = [
        {"errcode": 0, "access_token": "fixture-token", "expires_in": 7200},
        {"errcode": 0, "userid": "member"},
        {"errcode": 0, "name": "姓名", "avatar": "https://avatar.fixture/a.png"},
        {"errcode": 0, "userid": "member"},
        {"errcode": 48002, "errmsg": "permission denied"},
    ]
    seen = fake_requests(monkeypatch, replies)
    service = WeComAuthService()
    user = service.exchange_code(config, "code")
    assert user.user_id == "member" and user.name == "姓名"
    user = service.exchange_code(config, "another-code")
    assert user.user_id == "member" and user.name is None
    assert sum(url.endswith("/gettoken") for url, _ in seen) == 1
    assert seen[0][1]["params"] == {"corpid": "corp", "corpsecret": "fixture-secret"}
    assert seen[1][0] == "https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo"
    assert seen[1][1]["params"] == {"code": "code", "access_token": "fixture-token"}
    assert all(
        kwargs["allow_redirects"] is False and kwargs["timeout"] == 10
        for _, kwargs in seen
    )


def test_token_invalid_is_refreshed_once(monkeypatch, config):
    seen = fake_requests(
        monkeypatch,
        [
            {"errcode": 0, "access_token": "expired", "expires_in": 7200},
            {"errcode": 42001},
            {"errcode": 0, "access_token": "fresh", "expires_in": 7200},
            {"errcode": 0, "userid": "member"},
            {"errcode": 0, "name": "Member"},
        ],
    )
    assert WeComAuthService().exchange_code(config, "code").user_id == "member"
    assert seen[1][1]["params"]["access_token"] == "expired"
    assert seen[3][1]["params"]["access_token"] == "fresh"


def test_credentials_change_does_not_reuse_token(monkeypatch, config):
    seen = fake_requests(
        monkeypatch,
        [
            {"errcode": 0, "access_token": "first", "expires_in": 7200},
            {"errcode": 0, "userid": "member"},
            {"errcode": 0},
            {"errcode": 0, "access_token": "second", "expires_in": 7200},
            {"errcode": 0, "userid": "member"},
            {"errcode": 0},
        ],
    )
    service = WeComAuthService()
    service.exchange_code(config, "first-code")
    service.exchange_code(
        config.model_copy(update={"app_secret": "new-secret"}), "second-code"
    )
    assert seen[3][0].endswith("/gettoken")
    assert seen[4][1]["params"]["access_token"] == "second"


@pytest.mark.parametrize(
    "identity",
    [
        {"openid": "external"},
        {"external_userid": "external"},
        {},
        {"userid": " "},
    ],
)
def test_non_members_rejected(monkeypatch, config, identity):
    fake_requests(
        monkeypatch,
        [
            {"errcode": 0, "access_token": "token", "expires_in": 7200},
            {"errcode": 0, **identity},
        ],
    )
    with pytest.raises(WeComAPIError, match="仅允许") as error:
        WeComAuthService().exchange_code(config, "code")
    assert error.value.code == "WECOM_MEMBER_REQUIRED"


@pytest.mark.parametrize(
    "reply",
    [
        requests.ConnectionError("https://example?corpsecret=do-not-log"),
        {"errcode": 60020, "errmsg": "do-not-log"},
        [],
        {"errcode": 0},
        {"errcode": 0, "access_token": "token", "expires_in": -1},
    ],
)
def test_upstream_errors_do_not_expose_secrets(monkeypatch, config, reply, caplog):
    fake_requests(monkeypatch, [reply])
    with pytest.raises(WeComAPIError) as error:
        WeComAuthService().exchange_code(config, "sensitive-code")
    assert "do-not-log" not in str(error.value) + caplog.text
    assert "fixture-secret" not in str(error.value) + caplog.text
