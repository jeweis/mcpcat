"""从原始压缩流验证 OAuth 发现，避免响应夹具提前解压掩盖故障。"""

import gzip
import json
import zlib
from collections.abc import AsyncIterator

import httpx2
import pytest
from test_oauth_discovery import (
    ISSUER,
    PRM,
    URL,
    authorization_metadata,
    clients,
    protected_metadata,
)

from app.models.mcp_config import AuthStatus
from app.services.oauth_flow import OAuthFlowService
from app.services.oauth_metadata import _fetch, discover_oauth_endpoints
from app.services.oauth_probe import OAuthProbeError


class WireBytes(httpx2.AsyncByteStream):
    """将压缩字节分块交付，实际执行 HTTP 库的流式解码。"""

    def __init__(self, body: bytes) -> None:
        self.body = body

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for start in range(0, len(self.body), 17):
            yield self.body[start : start + 17]


def encoded_response(body: bytes, encoding: str) -> httpx2.Response:
    """保留原始编码头和压缩长度，模拟真实 HTTP 线上响应。"""
    wire = gzip.compress(body) if encoding == "gzip" else zlib.compress(body)
    return httpx2.Response(
        200,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Content-Encoding": encoding,
            "Content-Length": str(len(wire)),
            "X-Metadata-Fixture": "preserved",
        },
        stream=WireBytes(wire),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
@pytest.mark.parametrize("oidc", [False, True])
async def test_compressed_challenge_discovery(monkeypatch, encoding, oidc):
    visited = []

    def handler(request):
        visited.append(str(request.url))
        if str(request.url) == URL:
            return httpx2.Response(
                401, headers={"WWW-Authenticate": f'Bearer resource_metadata="{PRM}"'}
            )
        assert "Authorization" not in request.headers
        assert "Mcpcat-Key" not in request.headers
        if str(request.url) == PRM:
            data = protected_metadata()
        elif request.url.path == (
            "/.well-known/openid-configuration/tenant"
            if oidc
            else "/.well-known/oauth-authorization-server/tenant"
        ):
            data = {**authorization_metadata(), "fixture_note": "压缩元数据"}
        else:
            return httpx2.Response(404)
        return encoded_response(json.dumps(data, ensure_ascii=False).encode(), encoding)

    clients(monkeypatch, httpx2.MockTransport(handler))
    status, endpoints = await OAuthFlowService.inspect_auth_requirement(URL)
    assert status == AuthStatus.AUTH_REQUIRED
    assert endpoints["issuer"] == ISSUER
    assert endpoints["resource"] == URL
    assert endpoints["token_endpoint"] == "https://auth.fixture/token"
    assert len(visited) == (4 if oidc else 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
async def test_decoded_response_headers_match_body(encoding):
    body = '{"message":"压缩元数据"}'.encode()
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: encoded_response(body, encoding))
    ) as client:
        response = await _fetch(client, PRM)
    assert response.content == body
    assert "content-encoding" not in response.headers
    assert int(response.headers["content-length"]) == len(body)
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert response.headers["x-metadata-fixture"] == "preserved"
    assert str(response.request.url) == PRM


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
@pytest.mark.parametrize("resource", [False, True])
async def test_decoded_size_limit_is_enforced(monkeypatch, encoding, resource):
    def handler(request):
        if "oauth-protected-resource" in request.url.path and not resource:
            return httpx2.Response(200, json=protected_metadata())
        return encoded_response(b"x" * (256 * 1024 + 1), encoding)

    clients(monkeypatch, httpx2.MockTransport(handler))
    with pytest.raises(OAuthProbeError) as raised:
        await discover_oauth_endpoints(URL)
    assert raised.value.category == "oauth_metadata_size"


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
@pytest.mark.parametrize("resource", [False, True])
async def test_corrupt_encoding_fails_safely(monkeypatch, encoding, resource):
    def handler(request):
        if "oauth-protected-resource" in request.url.path and not resource:
            return httpx2.Response(200, json=protected_metadata())
        return httpx2.Response(
            200,
            headers={"Content-Encoding": encoding},
            stream=WireBytes(b"fixture-secret-not-valid-compressed-data"),
        )

    clients(monkeypatch, httpx2.MockTransport(handler))
    with pytest.raises(OAuthProbeError) as raised:
        await discover_oauth_endpoints(URL)
    assert raised.value.category == "oauth_metadata"
    assert "fixture-secret" not in str(raised.value)
