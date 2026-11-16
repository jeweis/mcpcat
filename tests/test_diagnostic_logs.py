"""诊断正文的权限、脱敏、阶段证据和有界查询回归。"""

import json
import logging
import sys
from datetime import datetime, timedelta, timezone

import anyio
import httpx2
import pytest
from fastmcp import Client, FastMCP
from test_skill_content_workflow import READ, WRITE, content_app  # noqa: F401

from app.services.diagnostic_logs import DiagnosticLogs, current_diagnostics
from app.services.diagnostic_redaction import redact
from app.services.inspector_service import InspectorService, InspectorSession
from app.services.proxy_clients import make_proxy_client_factory


def test_exception_chain_is_retained_but_credentials_are_removed() -> None:
    store = DiagnosticLogs()
    store.protect(
        {
            "headers": {"X-Upstream": "fixture-upstream-secret"},
            "env": {"CUSTOM_AUTH": "fixture-env-secret"},
        }
    )
    try:
        try:
            raise ConnectionError("fixture-upstream-secret refused connection")
        except ConnectionError as cause:
            raise RuntimeError(
                "fixture-env-secret url=https://user:password@example.test/mcp?custom=value "
                "api_key=unknown-key"
            ) from cause
    except RuntimeError as error:
        store.record(
            "初始化失败", server="time-demo", operation="initialize", error=error
        )
    result = store.query()
    text = json.dumps(result)
    for secret in [
        "fixture-upstream-secret",
        "fixture-env-secret",
        "unknown-key",
        "user:password",
        "custom=value",
    ]:
        assert secret not in text
    assert "ConnectionError" in text and "RuntimeError" in text
    assert "refused connection" in text
    assert result["items"][0]["operation"] == "initialize"


def test_query_bounds_cursor_filters_and_new_secrets() -> None:
    store = DiagnosticLogs(capacity=3)
    for index in range(5):
        store.record(
            f"entry-{index} fixture-new-secret",
            level="ERROR" if index % 2 else "INFO",
            server="alpha" if index % 2 else "beta",
            detail="x" * 17000,
        )
    store.protect({"api_key": "fixture-new-secret"})
    first = store.query(limit=2)
    assert first["retained"] == 3 and first["capacity"] == 3
    assert [item["id"] for item in first["items"]] == [5, 4]
    assert first["items"][0]["truncated"] is True
    assert "fixture-new-secret" not in json.dumps(first)
    store.record("newest")
    older = store.query(before=first["next_before"])
    assert [item["id"] for item in older["items"]] == []  # 被有界队列淘汰，不重复旧页。
    matches = store.query(level="ERROR", server="alpha", search="entry")
    assert [item["id"] for item in matches["items"]] == [4]
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    assert store.query(since=future)["items"] == []
    assert store.query(until=future)["items"]


@pytest.mark.parametrize(
    "value",
    [
        "Authorization: Bearer fixture-unknown-token",
        "Cookie: session=fixture-cookie",
        '{"client_secret":"fixture-client-secret"}',
        "password='fixture-password'",
        "https://name:fixture-password@example.test/mcp?access_token=fixture-token#private",
    ],
)
def test_unregistered_credential_formats(value: str) -> None:
    assert "fixture-" not in redact(value, set())


@pytest.mark.asyncio
async def test_real_subprocess_stderr_is_captured_on_failed_initialization() -> None:
    """真实子进程失败时保留 npm 原因，而非只剩协议连接关闭。"""
    store = DiagnosticLogs()
    token = current_diagnostics.set(store)
    code = (
        'import sys; print("npm error code ENOTEMPTY", file=sys.stderr); '
        'print("MYSQL_PASS=fixture-db-secret", file=sys.stderr); sys.exit(1)'
    )
    try:
        factory = make_proxy_client_factory(
            {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-c", code],
                "env": {"MYSQL_PASS": "fixture-db-secret"},
                "timeout": 2,
            },
            server_name="mysql-fixture",
        )
        with pytest.raises(Exception), anyio.fail_after(5):
            async with factory():
                pytest.fail("failed subprocess must not initialize")
    finally:
        current_diagnostics.reset(token)
    rows = store.query(server="mysql-fixture")["items"]
    assert any("ENOTEMPTY" in item["detail"] for item in rows)
    assert any(
        "ENOTEMPTY" in item["detail"] for item in store.query(level="ERROR")["items"]
    )
    assert any(item["operation"] == "stderr" for item in rows)
    assert any(item["operation"] == "upstream" for item in rows)
    assert "fixture-db-secret" not in json.dumps(rows)


@pytest.mark.asyncio
async def test_real_stdio_tools_still_work_and_no_newline_output_is_bounded() -> None:
    store = DiagnosticLogs(capacity=10)
    token = current_diagnostics.set(store)
    code = """
import sys
from fastmcp import FastMCP
print("x" * 20000, file=sys.stderr)
print("stderr fixture ready", file=sys.stderr)
mcp = FastMCP("diagnostic fixture")
@mcp.tool()
def ping() -> str:
    return "pong"
mcp.run(transport="stdio", show_banner=False)
"""
    try:
        factory = make_proxy_client_factory(
            {"type": "stdio", "command": sys.executable, "args": ["-c", code]},
            server_name="healthy-fixture",
        )
        with anyio.fail_after(15):
            async with factory() as client:
                assert [tool.name for tool in await client.list_tools()] == ["ping"]
                assert (await client.call_tool("ping")).data == "pong"
    finally:
        current_diagnostics.reset(token)
    rows = store.query()["items"]
    assert any("已省略" in item["detail"] for item in rows)
    assert any("stderr fixture ready" in item["detail"] for item in rows)
    assert all(len(item["detail"]) <= 4096 for item in rows)


@pytest.mark.asyncio
async def test_admin_endpoint_never_exposes_logs_to_read_or_anonymous(
    content_app,
) -> None:
    app, _ = content_app
    store = app.state.diagnostic_logs
    store.record(f"fixture failure {WRITE}", server="example", operation="tools/list")
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        assert (await client.get("/api/admin/logs")).status_code == 401
        assert (
            await client.get("/api/admin/logs", headers={"Mcpcat-Key": READ})
        ).status_code == 403
        response = await client.get(
            "/api/admin/logs",
            headers={"Mcpcat-Key": WRITE},
            params={"server": "example", "level": "ERROR"},
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        assert WRITE not in response.text and READ not in response.text
        assert response.json()["items"][0]["operation"] == "tools/list"
        for params in [
            {"limit": 201},
            {"before": -1},
            {"level": "invalid"},
            {"since": "2026-12-01", "until": "2026-01-01"},
        ]:
            assert (
                await client.get(
                    "/api/admin/logs", headers={"Mcpcat-Key": WRITE}, params=params
                )
            ).status_code == 422


@pytest.mark.asyncio
async def test_failed_connection_records_original_error_before_generic_response(
    monkeypatch,
) -> None:
    from app.services import inspector_service as module

    class BrokenClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            raise ConnectionError("upstream connection refused port 12345")

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(module, "Client", BrokenClient)
    store = DiagnosticLogs()
    token = current_diagnostics.set(store)
    try:
        with pytest.raises(ValueError, match="无法连接"):
            await InspectorService().create_session("broken", "http://127.0.0.1:1")
    finally:
        current_diagnostics.reset(token)
    row = store.query(server="broken")["items"][0]
    assert row["operation"] == "initialize"
    assert "ConnectionError" in row["detail"]
    assert "port 12345" in row["detail"]


@pytest.mark.asyncio
async def test_empty_tools_is_a_successful_discovery_with_explanation() -> None:
    service, store = InspectorService(), DiagnosticLogs()
    session = InspectorSession("empty", Client(FastMCP("empty")), "fixture-owner")
    service.sessions[session.id] = session
    token = current_diagnostics.set(store)
    try:
        assert await service.get_tools(session.id) == []
    finally:
        current_diagnostics.reset(token)
    row = store.query(server="empty")["items"][0]
    assert row["operation"] == "tools/list"
    assert "工具" in row["message"] or "tools" in row["message"]


@pytest.mark.asyncio
async def test_library_warning_is_collected_and_protects_config(content_app) -> None:
    app, _ = content_app
    logging.getLogger("app.fixture").warning(
        "request failed api_key=fixture-unknown-key"
    )
    text = json.dumps(app.state.diagnostic_logs.query())
    assert "request failed" in text
    assert "fixture-unknown-key" not in text
