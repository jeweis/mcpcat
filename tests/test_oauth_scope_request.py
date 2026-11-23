from urllib.parse import parse_qs, urlparse

from app.models.mcp_config import OAuthConfig
from app.services.oauth_authorization import begin_authorization


def _scope(config):
    result = begin_authorization("fixture", config)
    return parse_qs(urlparse(result["authorize_url"]).query).get("scope")


def _config(scopes):
    return OAuthConfig(
        authorization_endpoint="https://auth.fixture/authorize",
        client_id="client",
        scopes=scopes,
        code_challenge_methods_supported=["S256"],
    )


def test_star_preferred_over_full_scope_list():
    # 服务器常把 scopes_supported 全量列出，逐个校验会 invalid_scope；
    # 只要含 '*' 就只请求 '*'。
    assert _scope(_config(["*", "ExamTools", "SomeUnknownGroup"])) == ["*"]


def test_explicit_scopes_joined_when_no_star():
    assert _scope(_config(["read", "write"])) == ["read write"]


def test_no_scope_omitted():
    assert _scope(_config([])) is None
