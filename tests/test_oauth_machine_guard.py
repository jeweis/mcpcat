"""机器授权策略使用真实 SDK auth flow 与合成 HTTP 响应验证。"""

import httpx2
import pytest
from mcp.client.auth import OAuthFlowError
from mcp.client.auth.extensions.client_credentials import ClientCredentialsOAuthProvider

from app.services.oauth_machine_guard import MachineOAuthGuard, machine_http_client
from tests.test_m2m_provider_contract import (
    ISSUER,
    RESOURCE,
    SECRET,
    MemoryTokens,
    authorization_server,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["allowed", "challenge", "grant", "endpoint"])
async def test_machine_scope_and_destination_policy(case):
    exchanges, accesses = [], []
    store = MemoryTokens()
    handle = authorization_server(ISSUER, exchanges, accesses)

    def respond(request):
        response = handle(request)
        if (
            case == "challenge"
            and str(request.url) == RESOURCE
            and response.status_code == 401
        ):
            response.headers["WWW-Authenticate"] += ', scope="read write"'
        if case == "grant" and str(request.url) == ISSUER + "/token":
            return httpx2.Response(
                200,
                json={
                    "access_token": "synthetic-token",
                    "token_type": "Bearer",
                    "scope": "read write",
                    "expires_in": 3600,
                },
            )
        return response

    provider = ClientCredentialsOAuthProvider(
        server_url=RESOURCE,
        storage=store,
        client_id="fixture-client",
        client_secret=SECRET,
        issuer=ISSUER,
        scope="read",
    )
    guard = MachineOAuthGuard(
        provider,
        token_endpoint=ISSUER + ("/different" if case == "endpoint" else "/token"),
        scopes=["read"],
    )
    async with httpx2.AsyncClient(
        auth=guard, transport=httpx2.MockTransport(respond)
    ) as client:
        if case == "allowed":
            assert (await client.get(RESOURCE)).status_code == 200
            assert (await client.get(RESOURCE)).status_code == 200
            assert len(exchanges) == 1
            return
        with pytest.raises(OAuthFlowError) as error:
            await client.get(RESOURCE)
        assert SECRET not in str(error.value)
    assert accesses == [None]
    assert store.tokens is None
    assert len(exchanges) == (1 if case == "grant" else 0)


@pytest.mark.asyncio
async def test_scope_step_up_does_not_send_second_token_request():
    """已有 read 令牌也不能借 403 挑战静默获取 write。"""
    exchanges, accesses = [], []
    handle = authorization_server(ISSUER, exchanges, accesses)

    def respond(request):
        response = handle(request)
        if (
            str(request.url) == RESOURCE
            and response.status_code == 200
            and len(accesses) >= 3
        ):
            return httpx2.Response(
                403,
                headers={
                    "WWW-Authenticate": 'Bearer error="insufficient_scope", scope="write"'
                },
            )
        return response

    provider = ClientCredentialsOAuthProvider(
        server_url=RESOURCE,
        storage=MemoryTokens(),
        client_id="fixture-client",
        client_secret=SECRET,
        issuer=ISSUER,
        scope="read",
    )
    guard = MachineOAuthGuard(
        provider, token_endpoint=ISSUER + "/token", scopes=["read"]
    )
    async with httpx2.AsyncClient(
        auth=guard, transport=httpx2.MockTransport(respond)
    ) as client:
        assert (await client.get(RESOURCE)).status_code == 200
        with pytest.raises(OAuthFlowError, match="scope exceeds configured range"):
            await client.get(RESOURCE)
    assert len(exchanges) == 1
    assert accesses == [None, "Bearer synthetic-token", "Bearer synthetic-token"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("method", ["client_secret_basic", "client_secret_post"])
@pytest.mark.parametrize(
    "target", [ISSUER + "/diverted", "https://other.fixture/token"]
)
async def test_token_redirect_never_reaches_another_destination(status, method, target):
    """真实 HTTP 客户端重定向逻辑不能绕过认证流中的发送前校验。"""
    exchanges, accesses, diverted = [], [], []
    handle = authorization_server(ISSUER, exchanges, accesses)

    def respond(request):
        if str(request.url) == target:
            diverted.append(request)
            return httpx2.Response(
                200,
                json={
                    "access_token": "synthetic-token",
                    "token_type": "Bearer",
                },
            )
        response = handle(request)
        if str(request.url) == ISSUER + "/token":
            return httpx2.Response(status, headers={"location": target})
        return response

    provider = ClientCredentialsOAuthProvider(
        server_url=RESOURCE,
        storage=MemoryTokens(),
        client_id="fixture-client",
        client_secret=SECRET,
        issuer=ISSUER,
        scope="read",
        token_endpoint_auth_method=method,
    )
    guard = MachineOAuthGuard(
        provider, token_endpoint=ISSUER + "/token", scopes=["read"]
    )
    async with machine_http_client(
        auth=guard, transport=httpx2.MockTransport(respond), follow_redirects=True
    ) as client:
        with pytest.raises(OAuthFlowError):
            await client.get(RESOURCE)
    assert diverted == []
    assert len(exchanges) == 1
    assert accesses == [None]
