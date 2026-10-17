"""SDK DCR/CIMD/issuer 选择，使用固定本地传输，不创建真实第三方账户。"""

import json
from copy import deepcopy

import httpx2
import pytest
from test_oauth_authorization import config
from test_oauth_discovery import clients

from app.services.oauth_client_registration import (
    ClientIssuerMismatch,
    credentials_compatible,
    register_oauth_client,
    select_oauth_client,
)
from app.services.oauth_flow import OAuthFlowService
from app.services.oauth_tokens import current_access_token

REDIRECT = "https://gateway.fixture/api/oauth/callback/fixture"
DOCUMENT = "https://gateway.fixture/oauth/client.json"


def no_client(**kwargs):
    return config(
        client_id=None,
        client_secret=None,
        registration_endpoint="https://issuer.fixture/register/custom",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_cimd_explicit_supported_url_skips_dcr_and_secret(monkeypatch):
    original = no_client(
        client_metadata_url=DOCUMENT, client_id_metadata_document_supported=True
    )
    frozen = deepcopy(original.model_dump())
    calls = []
    clients(monkeypatch, httpx2.MockTransport(lambda request: calls.append(request)))
    selected, needed = await select_oauth_client(original, REDIRECT)
    assert not needed and not calls
    assert selected.client_id == DOCUMENT
    assert selected.client_secret is None
    assert selected.token_endpoint_auth_method == "none"
    assert selected.client_registration_mode == "cimd"
    assert original.model_dump() == frozen


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [False, None])
async def test_cimd_not_advertised_falls_back_dcr(monkeypatch, flag):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(
            201, json={"client_id": "registered", "client_secret": "fixture-secret"}
        )

    clients(monkeypatch, httpx2.MockTransport(handler))
    selected, needed = await select_oauth_client(
        no_client(
            client_metadata_url=DOCUMENT, client_id_metadata_document_supported=flag
        ),
        REDIRECT,
    )
    assert selected.client_id == "registered" and not needed
    assert selected.client_registration_mode == "dcr"
    assert selected.credentials_issuer == "https://issuer.fixture"
    assert str(requests[0].url).endswith("/register/custom")
    body = json.loads(requests[0].content)
    assert body["redirect_uris"] == [REDIRECT]
    assert body["application_type"] == "web"
    assert "client_secret" not in body
    assert "Authorization" not in requests[0].headers


@pytest.mark.asyncio
async def test_existing_client_precedes_cimd_and_dcr(monkeypatch):
    original = config(
        client_metadata_url=DOCUMENT,
        client_id_metadata_document_supported=True,
        registration_endpoint="https://issuer.fixture/register",
    )
    calls = []
    clients(monkeypatch, httpx2.MockTransport(lambda request: calls.append(request)))
    selected, needed = await select_oauth_client(original, REDIRECT)
    assert not needed and not calls
    assert selected.model_dump() == original.model_dump()


@pytest.mark.asyncio
async def test_bound_client_not_reused_or_reregistered_other_issuer(monkeypatch):
    calls = []
    clients(monkeypatch, httpx2.MockTransport(lambda request: calls.append(request)))
    with pytest.raises(ClientIssuerMismatch):
        await select_oauth_client(
            config(credentials_issuer="https://another.fixture"), REDIRECT
        )
    assert not calls


def test_cimd_portability_is_identity_not_url_shape_and_tokens_still_bound():
    portable = config(
        client_id=DOCUMENT,
        client_secret=None,
        client_metadata_url=DOCUMENT,
        issuer="https://new.fixture",
        credentials_issuer="https://old.fixture",
    )
    assert credentials_compatible(portable)
    assert not credentials_compatible(
        portable.model_copy(
            update={"client_metadata_url": "https://different.fixture/id"}
        )
    )
    assert not credentials_compatible(
        portable.model_copy(update={"client_metadata_url": None})
    )
    data = portable.model_dump()
    data.update(token={"access_token": "old"}, token_issuer="https://old.fixture")
    assert current_access_token({"oauth": data}) is None
    data["token_issuer"] = "https://new.fixture"
    assert current_access_token({"oauth": data}) == "old"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://gateway.fixture/client.json",
        "https://gateway.fixture/",
        "https://user:fixture-secret@gateway.fixture/client.json",
        "https://gateway.fixture/client.json#fragment",
    ],
)
async def test_invalid_cimd_never_registers_other_identity(monkeypatch, url):
    calls = []
    clients(monkeypatch, httpx2.MockTransport(lambda request: calls.append(request)))
    with pytest.raises(ValueError):
        await select_oauth_client(
            no_client(
                client_metadata_url=url, client_id_metadata_document_supported=True
            ),
            REDIRECT,
        )
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,secret",
    [
        ("none", None),
        ("client_secret_basic", "issued"),
        ("client_secret_post", "issued"),
    ],
)
async def test_dcr_response_method_and_local_issuer_binding(
    monkeypatch, method, secret
):
    def handler(request):
        return httpx2.Response(
            201,
            json={
                "client_id": "assigned",
                "client_secret": secret,
                "token_endpoint_auth_method": method,
                "redirect_uris": [REDIRECT],
                "issuer": "https://wire-attacker.fixture",
            },
        )

    clients(monkeypatch, httpx2.MockTransport(handler))
    selected, needed = await select_oauth_client(no_client(), REDIRECT)
    assert not needed
    assert selected.credentials_issuer == "https://issuer.fixture"
    assert selected.token_endpoint_auth_method == method
    assert selected.registered_redirect_uris == [REDIRECT]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"client_id": ""},
        {"client_id": "x", "token_endpoint_auth_method": "client_secret_basic"},
        {"client_id": "x", "token_endpoint_auth_method": "private_key_jwt"},
        {
            "client_id": "x",
            "token_endpoint_auth_method": "none",
            "redirect_uris": ["https://wrong.fixture/callback"],
        },
    ],
)
async def test_unusable_registration_returns_original_credentials_prompt(
    monkeypatch, body
):
    clients(
        monkeypatch,
        httpx2.MockTransport(lambda request: httpx2.Response(201, json=body)),
    )
    original = no_client(unknown={"preserve": True})
    selected, needed = await select_oauth_client(original, REDIRECT)
    assert needed and selected.model_dump() == original.model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 400, 500])
async def test_dcr_failure_no_redirect_or_secret_logging(monkeypatch, caplog, status):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx2.Response(
            status,
            headers={"Location": "https://redirect.fixture/register"},
            text="fixture-secret fixture-token",
        )

    clients(monkeypatch, httpx2.MockTransport(handler))
    assert await register_oauth_client(no_client(), REDIRECT) is None
    assert calls == ["https://issuer.fixture/register/custom"]
    assert "fixture-secret" not in caplog.text and "fixture-token" not in caplog.text


@pytest.mark.asyncio
async def test_legacy_registration_helper_retains_path_redirects_and_tuple(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(
            201, json={"client_id": "legacy", "client_secret": "fixture-secret"}
        )

    clients(monkeypatch, httpx2.MockTransport(handler))
    result = await OAuthFlowService.dynamic_client_register(
        "https://issuer.fixture/custom/register",
        [REDIRECT, "http://localhost:8765/callback"],
    )
    assert result == ("legacy", "fixture-secret")
    assert str(requests[0].url) == "https://issuer.fixture/custom/register"
    assert json.loads(requests[0].content)["redirect_uris"] == [
        REDIRECT,
        "http://localhost:8765/callback",
    ]


@pytest.mark.asyncio
async def test_dcr_changed_redirect_requires_explicit_configuration(monkeypatch):
    calls = []
    clients(monkeypatch, httpx2.MockTransport(lambda request: calls.append(request)))
    selected, needed = await select_oauth_client(
        config(
            client_registration_mode="dcr",
            registered_redirect_uris=["https://old.fixture/callback"],
        ),
        REDIRECT,
    )
    assert needed and selected.client_id == "fixture-client" and not calls
