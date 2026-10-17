"""原 REST 自动/手动回调通过实际 HTTP 请求交换 token，状态/来源失效不写入。"""

import base64
import hashlib
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import httpx2
import pytest
from fastapi import FastAPI
from test_oauth_authorization import config

from app.api import oauth
from app.services import oauth_authorization as authorization
from app.services.config_service import ConfigService
from app.services.oauth_flow import OAuthFlowService


@pytest.fixture
def callback_app(monkeypatch):
    authorization._auth_states.clear()
    settings = config(redirect_mode="auto").model_dump(exclude_none=True)
    info = {
        "config": {
            "type": "streamable-http",
            "url": "https://resource.fixture/mcp",
            "oauth": settings,
        },
        "status": "running",
        "auth_status": "none",
    }
    manager = SimpleNamespace(server_info={"fixture": info})
    manager._update_server_status = lambda name, status: manager.server_info[
        name
    ].update(status=status)
    monkeypatch.setattr(oauth, "server_manager", manager)
    monkeypatch.setattr(
        ConfigService, "get_public_base_url", lambda: "https://gateway.fixture"
    )
    saved = []
    monkeypatch.setattr(
        ConfigService, "update_server_oauth", lambda *args: saved.append(args) or True
    )
    app = FastAPI()
    app.include_router(oauth.router, prefix="/api")
    yield app, manager, info, saved
    authorization._auth_states.clear()


def token_client(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)),
    )


async def authorize(client, info, mode):
    info["config"]["oauth"]["redirect_mode"] = mode
    response = await client.post("/api/oauth/fixture/authorize")
    assert response.status_code == 200
    data = response.json()
    assert data["needs_credentials"] is False
    return data


async def complete(
    client, data, mode, *, issuer="https://issuer.fixture", callback_base=None
):
    parameters = {"code": "fixture-code", "state": data["state"]}
    if issuer is not None:
        parameters["iss"] = issuer
    if mode == "auto":
        return await client.get("/api/oauth/callback/fixture", params=parameters)
    url = (callback_base or data["redirect_uri"]) + "?" + urlencode(parameters)
    return await client.post("/api/oauth/fixture/callback", json={"callback_url": url})


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "manual"])
async def test_existing_rest_contract_issuer_pkce_and_replay(
    callback_app, monkeypatch, mode
):
    app, manager, info, saved = callback_app
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "fixture-access",
                "refresh_token": "fixture-refresh",
                "expires_in": 60,
            },
        )

    token_client(monkeypatch, handler)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="https://gateway.fixture"
    ) as client:
        data = await authorize(client, info, mode)
        response = await complete(client, data, mode)
        assert response.status_code == 200
        assert (await complete(client, data, mode)).status_code == 400
    assert len(requests) == 1
    values = parse_qs(requests[0].content.decode())
    digest = (
        base64.urlsafe_b64encode(
            hashlib.sha256(values["code_verifier"][0].encode()).digest()
        )
        .decode()
        .rstrip("=")
    )
    assert parse_qs(urlparse(data["authorize_url"]).query)["code_challenge"] == [digest]
    assert values["code"] == ["fixture-code"]
    assert values["redirect_uri"] == [data["redirect_uri"]]
    assert info["auth_status"] == "authorized"
    assert saved[-1][1].token.access_token == "fixture-access"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "manual"])
@pytest.mark.parametrize("issuer", [None, "https://wrong.fixture"])
async def test_bad_issuer_does_not_exchange_or_persist(
    callback_app, monkeypatch, mode, issuer
):
    app, manager, info, saved = callback_app
    requests = []
    token_client(monkeypatch, lambda request: requests.append(request))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="https://gateway.fixture"
    ) as client:
        data = await authorize(client, info, mode)
        assert (await complete(client, data, mode, issuer=issuer)).status_code == 400
    assert not requests and not saved
    assert "token" not in info["config"]["oauth"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["replace", "client", "url", "headers", "runtime", "stopped"]
)
async def test_source_changed_before_callback_never_sends_credentials(
    callback_app, monkeypatch, change
):
    app, manager, info, saved = callback_app
    requests = []
    token_client(monkeypatch, lambda request: requests.append(request))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="https://gateway.fixture"
    ) as client:
        data = await authorize(client, info, "auto")
        if change == "replace":
            manager.server_info["fixture"] = deepcopy(info)
        elif change == "client":
            info["config"]["oauth"]["client_secret"] = "new-secret"
        elif change == "runtime":
            info["mcp"] = object()
        elif change == "stopped":
            info["status"] = "stopped"
        else:
            info["config"][change] = "changed"
        assert (await complete(client, data, "auto")).status_code == 400
    assert not requests and not saved


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["replace", "client", "url", "storage_failure", "stopped"]
)
async def test_changed_during_token_exchange_not_persisted(
    callback_app, monkeypatch, change
):
    app, manager, info, saved = callback_app

    def handler(request):
        if change == "replace":
            manager.server_info["fixture"] = deepcopy(info)
        elif change == "client":
            info["config"]["oauth"]["client_id"] = "different-client"
        elif change == "url":
            info["config"]["url"] = "https://replacement.fixture/mcp"
        elif change == "stopped":
            info["status"] = "stopped"
        else:
            monkeypatch.setattr(
                ConfigService, "update_server_oauth", lambda *args: False
            )
        return httpx.Response(200, json={"access_token": "not-persisted"})

    token_client(monkeypatch, handler)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="https://gateway.fixture"
    ) as client:
        data = await authorize(client, info, "auto")
        assert (await complete(client, data, "auto")).status_code == 400
    assert not saved
    assert "token" not in manager.server_info["fixture"]["config"]["oauth"]


@pytest.mark.asyncio
async def test_bad_redirect_or_duplicate_query_no_token(callback_app, monkeypatch):
    app, manager, info, saved = callback_app
    requests = []
    token_client(monkeypatch, lambda request: requests.append(request))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="https://gateway.fixture"
    ) as client:
        data = await authorize(client, info, "manual")
        assert (
            await complete(
                client, data, "manual", callback_base="http://wrong.fixture/callback"
            )
        ).status_code == 400
        data = await authorize(client, info, "auto")
        query = (
            urlencode(
                {"code": "a", "state": data["state"], "iss": "https://issuer.fixture"}
            )
            + "&code=b"
        )
        assert (
            await client.get("/api/oauth/callback/fixture?" + query)
        ).status_code == 400
    assert not requests and not saved
