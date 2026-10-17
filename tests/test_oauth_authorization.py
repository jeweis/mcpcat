"""单次 state、冻结配置、RFC 9207 issuer 与旧手动授权兼容。"""

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest

from app.models.mcp_config import OAuthConfig
from app.services import oauth_authorization as authorization
from app.services.oauth_flow import OAuthFlowService


def config(**overrides):
    return OAuthConfig(
        **{
            "authorization_endpoint": "https://issuer.fixture/authorize?prompt=consent&state=old",
            "token_endpoint": "https://issuer.fixture/token",
            "client_id": "fixture-client",
            "client_secret": "fixture-secret",
            "issuer": "https://issuer.fixture",
            "authorization_response_iss_parameter_supported": True,
            "scopes": ["read"],
            **overrides,
        }
    )


@pytest.fixture(autouse=True)
def clean_states():
    authorization._auth_states.clear()
    yield
    authorization._auth_states.clear()


def test_public_sdk_pkce_url_and_immutable_snapshot():
    original = config(resource="https://resource.fixture/mcp")
    result = OAuthFlowService.generate_authorize_url("fixture", original)
    query = parse_qs(urlparse(result["authorize_url"]).query)
    entry = authorization._auth_states["fixture"]
    verifier = entry["code_verifier"]
    digest = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert 43 <= len(verifier) <= 128
    assert query["code_challenge"] == [digest]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == [result["state"]]
    assert query["prompt"] == ["consent"]
    assert query["resource"] == ["https://resource.fixture/mcp"]
    assert "client_secret" not in query
    original.client_secret = "changed"
    original.scopes.append("write")
    assert entry["oauth_config"].client_secret == "fixture-secret"
    assert entry["oauth_config"].scopes == ["read"]


@pytest.mark.parametrize(
    "issuer", [None, "https://wrong.fixture", "https://issuer.fixture/"]
)
def test_required_response_issuer_rejected_before_token(issuer):
    result = authorization.begin_authorization("fixture", config())
    assert (
        authorization.consume_authorization("fixture", result["state"], issuer=issuer)
        is None
    )
    assert "fixture" not in authorization._auth_states


def test_issuer_success_one_time_state_and_new_attempt_invalidates_previous():
    first = authorization.begin_authorization("fixture", config())
    second = authorization.begin_authorization("fixture", config())
    assert first["state"] != second["state"]
    assert authorization.consume_authorization("fixture", first["state"]) is None
    assert authorization.consume_authorization(
        "fixture", second["state"], issuer="https://issuer.fixture"
    )
    assert (
        authorization.consume_authorization(
            "fixture", second["state"], issuer="https://issuer.fixture"
        )
        is None
    )


def test_legacy_without_issuer_and_optional_issuer():
    legacy = config(issuer=None, authorization_response_iss_parameter_supported=False)
    result = authorization.begin_authorization("old", legacy)
    assert authorization.consume_authorization("old", result["state"])
    optional = config(authorization_response_iss_parameter_supported=False)
    result = authorization.begin_authorization("optional", optional)
    assert authorization.consume_authorization("optional", result["state"])


def test_wrong_state_or_other_server_cannot_consume_valid_attempt():
    result = authorization.begin_authorization("fixture", config())
    assert authorization.consume_authorization("another", result["state"]) is None
    assert authorization.consume_authorization("fixture", "wrong-state-中文") is None
    assert authorization.consume_authorization(
        "fixture", result["state"], issuer="https://issuer.fixture"
    )


def test_expiry_and_prune_use_monotonic_time(monkeypatch):
    monkeypatch.setattr(authorization.time, "monotonic", lambda: 100)
    first = authorization.begin_authorization("fixture", config())
    authorization.begin_authorization("abandoned", config())
    monkeypatch.setattr(authorization.time, "monotonic", lambda: 401)
    assert authorization.consume_authorization("fixture", first["state"]) is None
    authorization.begin_authorization("new", config())
    assert set(authorization._auth_states) == {"new"}


@pytest.mark.parametrize(
    "changed", ["client_id", "client_secret", "token_endpoint", "issuer", "resource"]
)
def test_changed_credentials_context_rejects(changed):
    original = config()
    result = authorization.begin_authorization("fixture", original)
    altered = original.model_copy(deep=True)
    setattr(altered, changed, "changed")
    assert (
        authorization.consume_authorization(
            "fixture",
            result["state"],
            issuer="https://issuer.fixture",
            current_config=altered,
        )
        is None
    )


def test_source_identity_cannot_be_reused_by_same_name_replacement():
    result = authorization.begin_authorization(
        "fixture", config(), source_identity=object()
    )
    assert (
        authorization.consume_authorization(
            "fixture",
            result["state"],
            issuer="https://issuer.fixture",
            source_identity=object(),
        )
        is None
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost:8765/callback",
        "http://localhost:8766/callback",
        "http://another.fixture:8765/callback",
        "http://localhost:8765/wrong",
        "http://user:fixture-secret@localhost:8765/callback",
        "http://localhost:8765/callback#fragment",
    ],
)
def test_manual_callback_redirect_target_rejected(url):
    result = authorization.begin_authorization("fixture", config())
    assert (
        authorization.consume_authorization(
            "fixture",
            result["state"],
            issuer="https://issuer.fixture",
            callback_url=url,
        )
        is None
    )


@pytest.mark.parametrize(
    "query",
    [
        "code=a&code=b&state=s",
        "code=a&state=s&state=b",
        "code=a&state=s&iss=a&iss=b",
        "code=&state=s",
        "code=a&state=",
        "code=a&state=s&iss=",
        "code=a&state=s&error=access_denied",
        "state=s",
    ],
)
def test_bad_callback_parameters(query):
    assert (
        authorization.parse_callback_url("http://localhost:8765/callback?" + query)
        is None
    )


def test_plain_pkce_refused_and_auto_redirect_preserved():
    with pytest.raises(ValueError):
        authorization.begin_authorization(
            "fixture", config(code_challenge_methods_supported=["plain"])
        )
    result = authorization.begin_authorization(
        "fixture", config(redirect_mode="auto"), "https://gateway.fixture/base/"
    )
    assert (
        result["redirect_uri"]
        == "https://gateway.fixture/base/api/oauth/callback/fixture"
    )
    assert result["redirect_mode"] == "auto"
