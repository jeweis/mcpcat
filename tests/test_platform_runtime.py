"""平台级运行环境、真实进程退出、协议回退与业务请求不重放。"""

import asyncio
import json
import sys
from copy import deepcopy

import httpx2
import pytest
from fastmcp import Client
from test_skill_content_workflow import READ, WRITE, content_app  # noqa: F401

from app.core.config import settings
from app.services.diagnostic_logs import DiagnosticLogs, current_diagnostics
from app.services.proxy_clients import make_proxy_client_factory
from app.services.runtime_attempts import classify
from app.services.runtime_environments import RuntimeEnvironment


@pytest.fixture(autouse=True)
def runtime_data(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "mcpcat_database_path", str(tmp_path / "runtime.db"))


LEGACY = """import json,sys,os
for line in sys.stdin:
    r=json.loads(line); m=r.get("method")
    if m=="server/discover":
        sys.exit(0)
    if "id" not in r: continue
    if m=="initialize": result={"protocolVersion":"2025-11-25","capabilities":{"tools":{}},"serverInfo":{"name":"old","version":"1"}}
    elif m=="tools/list": result={"tools":[{"name":"identity","inputSchema":{"type":"object"}}]}
    elif m=="tools/call":
        print("REQUEST_ONCE",file=sys.stderr,flush=True);sys.exit(7)
    else: result={}
    print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":result}),flush=True)
"""


def test_numeric_ports_are_not_authentication_evidence():
    assert classify("Connection closed on port 14031")[0] == "connection_closed"
    assert classify("HTTP status 403")[0] == "authentication"


def source(tmp_path, code=LEGACY):
    path = tmp_path / "server.py"
    path.write_text(code)
    return {"type": "stdio", "command": sys.executable, "args": [str(path)], "env": {}}


def test_environment_revisions_survive_restart_and_preserve_constraints(tmp_path):
    config = source(tmp_path)
    original = deepcopy(config)
    first = RuntimeEnvironment("one", config)
    again = RuntimeEnvironment("one", config)
    assert first.revision == again.revision
    assert first.snapshot()["dependency_manager"] == "native"
    assert first.environment() == config["env"]
    first.record({"stage": "ready", "ready": True, "protocol": "2025-11-25"})
    assert first.protocol_mode() == "legacy"
    fresh = first.select()
    assert fresh != first.revision
    first.select(first.revision)
    assert RuntimeEnvironment("one", config).revision == first.revision
    other = RuntimeEnvironment("two", config)
    assert first.directory != other.directory
    assert config == original
    metadata = (first.path / "index.json").read_text()
    assert config["command"] not in metadata
    config["env"] = {
        "TOKEN": "fixture-private-token",
        "UV_CACHE_DIR": str(tmp_path / "external"),
    }
    changed = RuntimeEnvironment("one", config)
    assert changed.revision != first.revision
    assert changed.environment()["UV_CACHE_DIR"] == config["env"]["UV_CACHE_DIR"]
    with pytest.raises(ValueError):
        changed.select(first.revision)
    assert "fixture-private-token" not in (changed.path / "index.json").read_text()


@pytest.mark.asyncio
async def test_legacy_discover_close_reconnects_fresh_and_never_replays_tool(tmp_path):
    config = source(tmp_path)
    factory = make_proxy_client_factory(config, server_name="legacy")
    store = DiagnosticLogs()
    token = current_diagnostics.set(store)
    try:
        client = factory()
        async with client:
            assert [tool.name for tool in await client.list_tools()] == ["identity"]
            with pytest.raises(Exception):
                await client.call_tool("identity", {})
    finally:
        current_diagnostics.reset(token)
    rows = store.query(limit=200)["items"]
    assert (
        sum(row["message"] == "初始化失败，使用新连接执行一次兼容恢复" for row in rows)
        == 1
    )
    assert (
        sum(
            "REQUEST_ONCE" in row["detail"] and row["operation"] == "stderr"
            for row in rows
        )
        == 1
    )
    assert any(
        row.get("exit_code") == 7 and row.get("stage") == "request" for row in rows
    )
    assert len({row["attempt_id"] for row in rows if row.get("attempt_id")}) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stderr,code,category",
    [
        ("ModuleNotFoundError: No module named 'other'", 1, "dependency"),
        ("Error: Cannot find module 'missing'", 1, "dependency"),
        ("Login failed for user", 2, "authentication"),
        ("certificate verify failed", 3, "tls"),
        ("ENOTFOUND example.test", 4, "dns"),
    ],
)
async def test_process_failure_keeps_real_exit_and_root_cause(
    tmp_path, stderr, code, category
):
    config = source(
        tmp_path,
        f"import sys; print({stderr!r},file=sys.stderr,flush=True);sys.exit({code})",
    )
    store = DiagnosticLogs()
    token = current_diagnostics.set(store)
    try:
        with pytest.raises(Exception):
            async with make_proxy_client_factory(config, server_name="failure")():
                pass
    finally:
        current_diagnostics.reset(token)
    rows = store.query()["items"]
    assert any(
        row.get("exit_code") == code and row.get("error_code") == category
        for row in rows
    )
    assert not any(row["operation"] == "recovery" for row in rows)


@pytest.mark.asyncio
async def test_generic_uv_tool_import_repair_is_not_package_specific(tmp_path):
    server = tmp_path / "good.py"
    server.write_text(
        LEGACY.replace(
            "sys.exit(0)",
            'print(json.dumps({"jsonrpc":"2.0","id":r["id"],"error":{"code":-32601,"message":"unsupported"}}),flush=True);continue',
        )
    )
    uvx = tmp_path / "uvx"
    uvx.write_text(f"""#!/bin/sh
case "$*" in
 *"--with mcp<2"*) exec "{sys.executable}" "{server}";;
 *) echo "ModuleNotFoundError: No module named 'mcp.server.fastmcp'" >&2;exit 1;;
esac
""")
    uvx.chmod(0o755)
    config = {
        "type": "stdio",
        "command": str(uvx),
        "args": ["another-python-mcp"],
        "env": {},
    }
    async with make_proxy_client_factory(config, server_name="generic")() as client:
        assert len(await client.list_tools()) == 1
    assert config["args"] == ["another-python-mcp"]


@pytest.mark.asyncio
async def test_admin_runtime_api_permissions_and_atomic_refresh(content_app):
    app, _ = content_app
    config = {
        "type": "stdio",
        "command": sys.executable,
        "args": [],
        "env": {"TOKEN": "fixture-private-token"},
    }
    app.state.server_manager.server_info = {
        "test": {"config": config, "status": "running"}
    }
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as api:
        assert (await api.get("/api/admin/runtime")).status_code == 401
        assert (
            await api.get("/api/admin/runtime", headers={"Mcpcat-Key": READ})
        ).status_code == 403
        headers = {"Mcpcat-Key": WRITE}
        first = await api.get("/api/admin/runtime/test", headers=headers)
        old = first.json()["environment"]["current"]
        assert "fixture-private-token" not in first.text
        env = RuntimeEnvironment("test", config)
        env.record({"stage": "ready", "ready": True})
        new = await api.post(
            "/api/admin/runtime/test/environment", json={}, headers=headers
        )
        assert new.status_code == 200 and new.json()["environment"]["current"] != old
        back = await api.post(
            "/api/admin/runtime/test/environment",
            json={"revision": old},
            headers=headers,
        )
        assert back.status_code == 200 and back.json()["environment"]["current"] == old


@pytest.mark.asyncio
async def test_remote_auth_and_network_errors_do_not_retry_legacy():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from test_gateway_client_capabilities import live_http

    app = FastAPI()

    @app.post("/mcp")
    async def deny():
        return JSONResponse({"detail": "Forbidden"}, status_code=403)

    store = DiagnosticLogs()
    token = current_diagnostics.set(store)
    try:
        async with live_http(app) as url:
            with pytest.raises(Exception):
                async with make_proxy_client_factory(
                    {"type": "streamable-http", "url": url}, server_name="remote-auth"
                )():
                    pass
        import socket

        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            unavailable = reserved.getsockname()[1]
        with pytest.raises(Exception):
            async with make_proxy_client_factory(
                {
                    "type": "streamable-http",
                    "url": f"http://127.0.0.1:{unavailable}/mcp",
                },
                server_name="remote-network",
            )():
                pass
    finally:
        current_diagnostics.reset(token)
    rows = store.query(limit=200)["items"]
    assert not any(row["operation"] == "recovery" for row in rows)
    assert any(row.get("error_code") == "authentication" for row in rows), [
        (row.get("error_code"), row["message"], row["detail"][:500]) for row in rows
    ]
    assert any(row.get("error_code") in {"network", "http"} for row in rows), [
        (row.get("error_code"), row["message"], row["detail"][:500]) for row in rows
    ]


@pytest.mark.asyncio
async def test_http_transport_error_keeps_network_evidence():
    from app.services.runtime_attempts import RuntimeAttempt
    from app.services.runtime_http import RuntimeHttpClient

    def refused(request):
        raise httpx2.ConnectError("Connection refused", request=request)

    attempt = RuntimeAttempt("network-fixture")
    async with RuntimeHttpClient(
        attempt=attempt, transport=httpx2.MockTransport(refused)
    ) as client:
        with pytest.raises(httpx2.ConnectError):
            await client.get("http://fixture.test/mcp")
    assert classify(attempt.stderr)[0] == "network"


def test_package_snapshot_uses_observed_environment_instead_of_mixing_versions(
    tmp_path,
):
    from app.services.runtime_packages import package_snapshot

    cache = tmp_path / "cache"
    active = cache / "uv" / "archive-v0" / "active"
    good = active / "lib" / "python3.12" / "site-packages" / "mcp-1.dist-info"
    bad = cache / "uv" / "archive-v0" / "other" / "mcp-2.dist-info"
    good.mkdir(parents=True)
    bad.mkdir(parents=True)
    (good / "METADATA").write_text("Name: mcp\nVersion: 1.28.1\n")
    (bad / "METADATA").write_text("Name: mcp\nVersion: 2.3.0\n")
    rows = package_snapshot(cache, {"python_environment": str(active)})
    assert rows == [{"runtime": "python", "name": "mcp", "version": "1.28.1"}]


def test_diagnostic_credentials_do_not_hide_permission_words_or_stages():
    store = DiagnosticLogs()
    store.protect(
        {
            "api_keys": [
                {"key": "fixture-secret-key", "permission": "read", "name": "reader"}
            ]
        }
    )
    store.record(
        "ready fixture-secret-key", attempt_id="fixture-attempt", stage="ready"
    )
    row = store.query()["items"][0]
    assert row["stage"] == "ready"
    assert row["message"] == "ready [REDACTED]"


@pytest.mark.asyncio
async def test_cache_corruption_rotates_revision_once_without_deleting_history(
    tmp_path,
):
    server = tmp_path / "server.py"
    server.write_text(
        LEGACY.replace(
            "sys.exit(0)",
            'print(json.dumps({"jsonrpc":"2.0","id":r["id"],"error":{"code":-32601,"message":"legacy"}}),flush=True);continue',
        )
    )
    marker = tmp_path / "first"
    npx = tmp_path / "npx"
    npx.write_text(f"""#!/bin/sh
if [ ! -e "{marker}" ]; then
  touch "{marker}"
  echo 'npm error ENOTEMPTY cache collision' >&2
  exit 1
fi
exec "{sys.executable}" "{server}"
""")
    npx.chmod(0o755)
    config = {
        "type": "stdio",
        "command": str(npx),
        "args": ["fixture-package"],
        "env": {},
    }
    old = RuntimeEnvironment("cache-fixture", config)
    async with make_proxy_client_factory(
        config, server_name="cache-fixture"
    )() as client:
        assert len(await client.list_tools()) == 1
    current = RuntimeEnvironment("cache-fixture", config)
    assert current.revision != old.revision
    assert old.directory.exists()
    assert len(current.snapshot()["revisions"]) == 2
