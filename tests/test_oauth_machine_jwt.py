"""私钥 JWT 通过生产装配和 SDK 交换；密钥每次测试内生成。"""

from urllib.parse import parse_qs

import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.services.oauth_machine_client import machine_client_options
from app.services.oauth_machine_jwt import machine_assertion_provider
from tests.test_m2m_provider_contract import ISSUER, RESOURCE, authorization_server
from tests.test_oauth_machine_client import configuration


@pytest.mark.asyncio
@pytest.mark.parametrize("algorithm", ["RS256", "ES256"])
async def test_signed_assertion_verified_by_authorization_server(
    monkeypatch, algorithm
):
    key = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if algorithm == "RS256"
        else ec.generate_private_key(ec.SECP256R1())
    )
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    monkeypatch.setenv("MCPCAT_TEST_SIGNING_KEY", pem)
    config = configuration()
    config["oauth"].update(
        token_endpoint_auth_method="private_key_jwt",
        private_key_env="MCPCAT_TEST_SIGNING_KEY",
        signing_algorithm=algorithm,
    )
    del config["oauth"]["client_secret"]
    exchanges, accesses, assertions = [], [], []
    handle = authorization_server(ISSUER, exchanges, accesses)

    def respond(request):
        if str(request.url) == ISSUER + "/token":
            form = parse_qs(request.content.decode())
            claims = jwt.decode(
                form["client_assertion"][0],
                key.public_key(),
                algorithms=[algorithm],
                audience=ISSUER,
                issuer="fixture-client",
            )
            assert claims["sub"] == "fixture-client"
            assert claims["exp"] - claims["iat"] == 60
            assert "client_secret" not in form
            assert pem not in request.content.decode()
            assertions.append(claims["jti"])
        return handle(request)

    for _ in range(2):
        options = machine_client_options(config)
        async with options["httpx_client_factory"](
            auth=options["auth"], transport=httpx2.MockTransport(respond)
        ) as client:
            assert (await client.get(RESOURCE)).status_code == 200
    assert len(set(assertions)) == 2
    assert len(exchanges) == 2
    assert pem not in str(config)


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"private_key_env": "../key"},
        {"private_key_env": "MCPCAT_TEST_MISSING_KEY"},
        {"private_key_env": "MCPCAT_TEST_BAD_KEY", "signing_algorithm": "HS256"},
        {"private_key_env": "MCPCAT_TEST_BAD_KEY"},
    ],
)
def test_invalid_private_key_configuration_is_safe(monkeypatch, settings):
    monkeypatch.delenv("MCPCAT_TEST_MISSING_KEY", raising=False)
    monkeypatch.setenv("MCPCAT_TEST_BAD_KEY", "synthetic-secret-not-a-key")
    with pytest.raises(ValueError) as error:
        machine_assertion_provider(settings, "fixture-client")
    assert "synthetic-secret-not-a-key" not in str(error.value)
