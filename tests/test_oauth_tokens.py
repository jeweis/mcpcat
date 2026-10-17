"""旧 token 默认格式、刷新及新连接实时取凭证，不使用用户存储或真实网络。"""

from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import parse_qs

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.dependencies import get_http_headers
from test_oauth_authorization import config
from test_oauth_callbacks import token_client
from test_upstream_apps_proxy import proxy_targets

from app.models.mcp_config import OAuthToken
from app.services.mcp_factory import MCPServerFactory
from app.services.oauth_flow import OAuthFlowService
from app.services.oauth_tokens import current_access_token, request_oauth_token


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method", [None, "client_secret_post", "client_secret_basic", "none"]
)
async def test_refresh_preserves_legacy_default_and_known_auth_methods(
    monkeypatch, method
):
    original = config(
        issuer=None,
        token=OAuthToken(access_token="old", refresh_token="previous"),
        token_endpoint_auth_method=method,
        resource="https://resource.fixture/mcp",
    )
    frozen = deepcopy(original.model_dump())
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, json={"access_token": "new", "token_type": "bearer", "expires_in": 0}
        )

    token_client(monkeypatch, handler)
    result = await OAuthFlowService.refresh_oauth_token("old", original)
    assert result.access_token == "new"
    assert result.refresh_token == "previous"
    assert result.token_type == "Bearer"
    assert datetime.fromisoformat(result.expires_at) <= datetime.now(timezone.utc)
    assert original.model_dump() == frozen
    data = parse_qs(requests[0].content.decode())
    assert data["grant_type"] == ["refresh_token"]
    assert data["resource"] == ["https://resource.fixture/mcp"]
    assert data["refresh_token"] == ["previous"]
    if method == "client_secret_basic":
        assert requests[0].headers["Authorization"].startswith("Basic ")
        assert "client_secret" not in data
    elif method == "none":
        assert "client_secret" not in data
        assert "Authorization" not in requests[0].headers
    else:
        assert data["client_secret"] == ["fixture-secret"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"access_token": ""},
        {"access_token": "a", "token_type": "MAC"},
        {"access_token": "a", "expires_in": "bad"},
    ],
)
async def test_invalid_token_response_not_success(monkeypatch, body):
    token_client(monkeypatch, lambda request: httpx.Response(200, json=body))
    assert (
        await request_oauth_token(
            config(), {"grant_type": "authorization_code", "code": "fixture-code"}
        )
        is None
    )


@pytest.mark.asyncio
async def test_token_redirect_and_reflected_secrets_not_logged(monkeypatch, caplog):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(
            307,
            headers={"Location": "https://redirect.fixture/token"},
            text="fixture-secret fixture-code fixture-refresh",
        )

    token_client(monkeypatch, handler)
    assert (
        await request_oauth_token(
            config(), {"grant_type": "authorization_code", "code": "fixture-code"}
        )
        is None
    )
    assert requested == ["https://issuer.fixture/token"]
    for secret in ("fixture-secret", "fixture-code", "fixture-refresh"):
        assert secret not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["credentials_issuer", "token_issuer"])
async def test_bound_refresh_not_sent_to_different_issuer(monkeypatch, field):
    cfg = config(
        token=OAuthToken(access_token="old", refresh_token="fixture-refresh"),
        **{field: "https://another.fixture"},
    )
    requests = []
    token_client(monkeypatch, lambda request: requests.append(request))
    assert await OAuthFlowService.refresh_oauth_token("fixture", cfg) is None
    assert not requests


@pytest.mark.parametrize("expiry", ["2000-01-01T00:00:00+00:00", "bad", 123])
def test_expired_or_malformed_expiry_never_sent(expiry):
    assert (
        current_access_token(
            {"oauth": {"token": {"access_token": "old", "expires_at": expiry}}}
        )
        is None
    )


def test_legacy_token_with_no_new_binding_is_preserved():
    assert (
        current_access_token({"oauth": {"token": {"access_token": "legacy"}}})
        == "legacy"
    )
    assert (
        current_access_token(
            {
                "oauth": {
                    "issuer": "https://issuer.fixture/",
                    "token_issuer": "https://issuer.fixture",
                    "token": {"access_token": "bound"},
                }
            }
        )
        == "bound"
    )
    assert (
        current_access_token(
            {
                "oauth": {
                    "issuer": "https://changed.fixture",
                    "token_issuer": "https://issuer.fixture",
                    "token": {"access_token": "bound"},
                }
            }
        )
        is None
    )


def test_legacy_expiry_helper_fails_closed_for_malformed_expiry():
    assert OAuthFlowService.is_token_expired(
        OAuthToken(access_token="fixture", expires_at="bad")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_actual_proxy_new_connections_use_refreshed_or_revoked_token(
    monkeypatch, mode
):
    source = FastMCP("token-fixture")

    @source.tool()
    def identity() -> str:
        return get_http_headers(include={"authorization"}).get(
            "authorization", "absent"
        )

    app = source.http_app(path="/mcp", json_response=True)
    url = "https://resource.fixture/mcp"
    proxy_targets(monkeypatch, {url: app})
    configuration = {
        "type": "streamable-http",
        "url": url,
        "timeout": 3,
        "oauth": {"token": {"access_token": "first"}},
    }
    proxy = MCPServerFactory.create_server("fixture", configuration)
    async with app.lifespan(app), Client(proxy, mode=mode) as client:
        assert (await client.call_tool("identity", {})).data == "Bearer first"
        configuration["oauth"]["token"] = {"access_token": "refreshed"}
        assert (await client.call_tool("identity", {})).data == "Bearer refreshed"
        configuration["oauth"].pop("token")
        assert (await client.call_tool("identity", {})).data == "absent"
        configuration["oauth"]["token"] = {
            "access_token": "expired",
            "expires_at": "2000-01-01",
        }
        assert (await client.call_tool("identity", {})).data == "absent"
