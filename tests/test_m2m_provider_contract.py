"""固定 SDK 机器授权公开契约；合成 HTTP 授权端点，无真实凭证/网络。"""

from urllib.parse import parse_qs

import httpx2
import pytest
from mcp.client.auth import OAuthFlowError
from mcp.client.auth.extensions.client_credentials import (
    ClientCredentialsOAuthProvider,
    PrivateKeyJWTOAuthProvider,
)

RESOURCE = "https://resource.fixture/mcp"
ISSUER = "https://issuer.fixture"
SECRET = "synthetic-client-secret"


class MemoryTokens:
    """每个来源独立缓存；只实现 SDK 公开存储协议。"""

    def __init__(self):
        self.tokens = self.client_info = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client_info

    async def set_client_info(self, info):
        self.client_info = info


def authorization_server(issuer, exchanges, accesses):
    """记录真实 auth flow 产生的 HTTP 请求，不替换 Provider 内部状态。"""

    def handle(request):
        url = str(request.url)
        if url == RESOURCE:
            accesses.append(request.headers.get("authorization"))
            if request.headers.get("authorization") == "Bearer synthetic-token":
                return httpx2.Response(200, json={"ok": True})
            return httpx2.Response(
                401,
                headers={
                    "WWW-Authenticate": 'Bearer resource_metadata="https://resource.fixture/metadata"'
                },
            )
        if url == "https://resource.fixture/metadata":
            return httpx2.Response(
                200,
                json={
                    "resource": RESOURCE,
                    "authorization_servers": [issuer],
                    "scopes_supported": ["read"],
                },
            )
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx2.Response(
                200,
                json={
                    "issuer": issuer,
                    "token_endpoint": issuer + "/token",
                    "authorization_endpoint": issuer + "/authorize",
                    "grant_types_supported": ["client_credentials"],
                    "token_endpoint_auth_methods_supported": [
                        "client_secret_basic",
                        "client_secret_post",
                    ],
                    "scopes_supported": ["read"],
                },
            )
        if url == issuer + "/token":
            exchanges.append(request)
            return httpx2.Response(
                200,
                json={
                    "access_token": "synthetic-token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        return httpx2.Response(404)

    return handle


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["client_secret_basic", "client_secret_post"])
async def test_sdk_pinned_m2m_acquires_token_without_browser_or_registration(method):
    exchanges, accesses = [], []
    provider = ClientCredentialsOAuthProvider(
        server_url=RESOURCE,
        storage=MemoryTokens(),
        client_id="fixture-client",
        client_secret=SECRET,
        issuer=ISSUER,
        scope="read",
        token_endpoint_auth_method=method,
    )
    async with httpx2.AsyncClient(
        auth=provider,
        transport=httpx2.MockTransport(
            authorization_server(ISSUER, exchanges, accesses)
        ),
    ) as client:
        assert (await client.get(RESOURCE)).status_code == 200
        assert (await client.get(RESOURCE)).status_code == 200
    assert len(exchanges) == 1
    form = parse_qs(exchanges[0].content.decode())
    assert form["grant_type"] == ["client_credentials"]
    assert form["scope"] == ["read"]
    assert accesses == [None, "Bearer synthetic-token", "Bearer synthetic-token"]
    if method == "client_secret_basic":
        assert exchanges[0].headers["authorization"].startswith("Basic ")
        assert "client_secret" not in form
    else:
        assert form["client_secret"] == [SECRET]


@pytest.mark.asyncio
async def test_sdk_pinned_issuer_rejects_credential_redirect():
    exchanges, accesses = [], []
    provider = ClientCredentialsOAuthProvider(
        server_url=RESOURCE,
        storage=MemoryTokens(),
        client_id="fixture-client",
        client_secret=SECRET,
        issuer=ISSUER,
    )
    async with httpx2.AsyncClient(
        auth=provider,
        transport=httpx2.MockTransport(
            authorization_server("https://other.fixture", exchanges, accesses)
        ),
    ) as client:
        with pytest.raises(OAuthFlowError):
            await client.get(RESOURCE)
    assert exchanges == []
    assert accesses == [None]


@pytest.mark.asyncio
@pytest.mark.parametrize("issuer", [ISSUER, "https://other.fixture"])
async def test_private_key_assertion_is_minted_only_for_pinned_issuer(issuer):
    """验证公开签名回调的调用边界；此测试不声称验证 JWT 密码学。"""
    exchanges, accesses, audiences = [], [], []

    async def assertion(audience: str) -> str:
        audiences.append(audience)
        return "synthetic-signed-assertion"

    provider = PrivateKeyJWTOAuthProvider(
        server_url=RESOURCE,
        storage=MemoryTokens(),
        client_id="fixture-client",
        assertion_provider=assertion,
        issuer=ISSUER,
        scope="read",
    )
    async with httpx2.AsyncClient(
        auth=provider,
        transport=httpx2.MockTransport(
            authorization_server(issuer, exchanges, accesses)
        ),
    ) as client:
        if issuer != ISSUER:
            with pytest.raises(OAuthFlowError):
                await client.get(RESOURCE)
            assert audiences == exchanges == []
            return
        assert (await client.get(RESOURCE)).status_code == 200
    assert audiences == [ISSUER]
    form = parse_qs(exchanges[0].content.decode())
    assert form["grant_type"] == ["client_credentials"]
    assert form["client_assertion"] == ["synthetic-signed-assertion"]
    assert form["client_assertion_type"] == [
        "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
    ]
    assert "client_secret" not in form


@pytest.mark.asyncio
async def test_sdk_scope_is_not_an_administrator_permission_ceiling():
    """记录 SDK 原生行为：网关不能把 scope 参数当成已实施的范围限制。"""
    exchanges, accesses = [], []
    handle = authorization_server(ISSUER, exchanges, accesses)

    def challenge(request):
        response = handle(request)
        if str(request.url) == RESOURCE and response.status_code == 401:
            response.headers["WWW-Authenticate"] += ', scope="read write"'
        return response

    provider = ClientCredentialsOAuthProvider(
        server_url=RESOURCE,
        storage=MemoryTokens(),
        client_id="fixture-client",
        client_secret=SECRET,
        issuer=ISSUER,
        scope="read",
    )
    async with httpx2.AsyncClient(
        auth=provider, transport=httpx2.MockTransport(challenge)
    ) as client:
        assert (await client.get(RESOURCE)).status_code == 200
    assert parse_qs(exchanges[0].content.decode())["scope"] == ["read write"]
