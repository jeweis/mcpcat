"""生产客户端工厂的机器认证、配置失败与旧授权兼容。"""

from copy import deepcopy
from contextlib import asynccontextmanager
import json

import httpx2
import pytest
from fastmcp import Context, FastMCP
from mcp.client.extension import advertise

from app.services import oauth_machine_client as machine
from app.services import oauth_machine_probe
from app.services.inspector_auth import diagnose_upstream
from app.services.proxy_clients import make_proxy_client_factory
from tests.test_oauth_probe_manager import manager_fixture
from tests.test_proxy_cross_generation import single_era
from tests.test_m2m_provider_contract import (
    ISSUER,
    RESOURCE,
    SECRET,
    authorization_server,
)


def configuration():
    return {
        "type": "streamable-http",
        "url": RESOURCE,
        "oauth": {
            "grant_type": "client_credentials",
            "issuer": ISSUER,
            "token_endpoint": ISSUER + "/token",
            "client_id": "fixture-client",
            "client_secret": SECRET,
            "scopes": ["read"],
            "token": {"access_token": "old-interactive-token"},
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("replace", [False, True])
async def test_machine_probe_failure_is_safe_and_generation_bound(monkeypatch, replace):
    manager, config = manager_fixture()
    config.update(configuration())
    replacement = {"config": {}, "status": "running", "auth_status": "none"}

    @asynccontextmanager
    async def failing():
        if replace:
            manager.server_info["fixture"] = replacement
        raise RuntimeError(SECRET)
        yield

    monkeypatch.setattr(
        oauth_machine_probe, "make_proxy_client_factory", lambda _: failing
    )
    await manager._detect_auth_status("fixture", config)
    result = manager.server_info["fixture"]
    if replace:
        assert result is replacement
        assert result["auth_status"] == "none"
    else:
        assert result["status"] == "failed"
        assert SECRET not in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("machine_mode", [True, False])
@pytest.mark.parametrize("era", ["modern", "legacy"])
async def test_factory_machine_auth_reads_actual_mcp_tools(
    monkeypatch, machine_mode, era
):
    source = FastMCP("machine-source")

    @source.tool
    def echo(value: str, ctx: Context) -> dict:
        return {"value": value, "protocol": ctx.request_context.protocol_version}

    app = source.http_app(path="/mcp", json_response=True)
    exchanges, accesses = [], []
    advertised = []
    methods = []
    handle = authorization_server(ISSUER, exchanges, accesses)
    negotiation = []
    asgi = httpx2.ASGITransport(app=single_era(app, era, negotiation))
    expected_protocol = "2026-07-28" if era == "modern" else "2025-11-25"

    async def respond(request):
        if str(request.url) == RESOURCE and request.method == "POST":
            body = json.loads(request.content)
            methods.append(body.get("method"))
            capabilities = (
                body.get("params", {})
                .get("_meta", {})
                .get("io.modelcontextprotocol/clientCapabilities", {})
            )
            if body.get("method") == "initialize":
                capabilities = body["params"]["capabilities"]
            if capabilities:
                advertised.append(capabilities.get("extensions", {}))
        response = handle(request)
        if str(request.url) == RESOURCE and response.status_code == 200:
            # ASGITransport buffers the complete response; an idle legacy GET
            # stream never completes and holds the SDK's OAuth flow lock.
            # This JSON-only fixture explicitly declines that optional stream.
            if request.method == "GET":
                return httpx2.Response(405, headers={"Allow": "POST, DELETE"})
            return await asgi.handle_async_request(request)
        return response

    original = machine.machine_http_client
    monkeypatch.setattr(
        machine,
        "machine_http_client",
        lambda **kwargs: original(**kwargs, transport=httpx2.MockTransport(respond)),
    )
    config = configuration()
    if not machine_mode:
        del config["oauth"]["grant_type"]
        config["oauth"]["token"]["access_token"] = "synthetic-token"
        # 旧路径也使用真实 HTTP transport，只注入隔离 HTTP 响应。
        from app.services import proxy_clients

        original_transport = proxy_clients.StreamableHttpTransport
        monkeypatch.setattr(
            proxy_clients,
            "StreamableHttpTransport",
            lambda **kwargs: original_transport(
                **kwargs, httpx_client_factory=machine.machine_http_client
            ),
        )
    before = deepcopy(config)
    factory = make_proxy_client_factory(config)
    async with app.router.lifespan_context(app):
        if machine_mode:
            manager, managed_config = manager_fixture()
            managed_config.update(deepcopy(config))
            await manager._detect_auth_status("fixture", managed_config)
            assert manager.server_info["fixture"]["auth_status"] == "authorized"
            assert managed_config["oauth"] == config["oauth"]
            methods.clear()
            diagnostic = await diagnose_upstream(manager.server_info["fixture"])
            assert diagnostic["status"] == "reachable"
            assert diagnostic["machine_auth"] is True
            assert diagnostic["protocol"] == expected_protocol
            assert not any(method.startswith("tools/") for method in methods)
        extras = [advertise("com.example/test")]
        for _ in range(2):
            async with factory(extensions=extras) as client:
                assert [tool.name for tool in await client.list_tools()] == ["echo"]
                result = await client.call_tool("echo", {"value": "works"})
                assert result.data == {"value": "works", "protocol": expected_protocol}
                assert client.protocol_version == expected_protocol
        assert len(extras) == 1
    assert len(exchanges) == (4 if machine_mode else 0)
    assert advertised
    assert all(
        ("io.modelcontextprotocol/oauth-client-credentials" in item) == machine_mode
        for item in advertised
    )
    assert any("com.example/test" in item for item in advertised)
    assert "Bearer old-interactive-token" not in accesses
    assert config == before
    assert ("initialize" in negotiation) == (era == "legacy")


@pytest.mark.parametrize(
    "field,value",
    [
        ("issuer", None),
        ("issuer", "http://untrusted.fixture"),
        ("token_endpoint", "https://user:secret@issuer.fixture/token"),
        ("client_secret", ""),
        ("scopes", ["read write"]),
        ("scopes", "read"),
        ("credentials_issuer", "https://other.fixture"),
    ],
)
def test_bad_machine_configuration_fails_closed(field, value):
    config = configuration()
    config["oauth"][field] = value
    with pytest.raises(ValueError) as error:
        make_proxy_client_factory(config)()
    assert SECRET not in str(error.value)


def test_old_oauth_and_static_header_are_not_machine_auth():
    config = configuration()
    del config["oauth"]["grant_type"]
    assert machine.machine_client_options(config) is None
    assert (
        machine.machine_client_options({"headers": {"Authorization": "Bearer fixture"}})
        is None
    )
