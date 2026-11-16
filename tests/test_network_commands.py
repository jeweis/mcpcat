"""网络诊断真实子进程、权限、隔离、取消及输出边界。"""

import asyncio
import json
import os
import sys

import httpx2
import pytest
from pydantic import ValidationError
from test_skill_content_workflow import READ, WRITE, content_app  # noqa: F401

from app.services.diagnostic_logs import DiagnosticLogs
from app.services.network_command_runner import NetworkCommandRunner
from app.services.network_commands import NetworkCommand, command_args


def command(**kwargs):
    return NetworkCommand(kind="dns", target="localhost", **kwargs)


async def collect(runner, source, **kwargs):
    return [
        json.loads(item)
        async for item in runner.events(
            command(**kwargs), [sys.executable, "-u", "-c", source]
        )
    ]


@pytest.mark.parametrize(
    "kind,target",
    [
        ("ping", "-c 10 localhost"),
        ("dns", "host; id"),
        ("tcp", "$(whoami)"),
        ("http", "file:///etc/passwd"),
        ("http", "http://user:pass@localhost"),
        ("http", "http://localhost:bad"),
        ("http", "http://localhost/\nInjected"),
    ],
)
def test_rejects_non_diagnostic_targets(kind, target):
    with pytest.raises(ValidationError):
        NetworkCommand(kind=kind, target=target)


@pytest.mark.asyncio
async def test_real_dns_and_environment_and_redaction(monkeypatch):
    store = DiagnosticLogs()
    store.protect({"api_key": "fixture-network-secret"})
    runner = NetworkCommandRunner(store)
    args = command_args(command())
    result = [json.loads(item) async for item in runner.events(command(), args)]
    assert result[-1]["exit_code"] == 0
    assert "127.0.0.1" in json.dumps(result) or "::1" in json.dumps(result)
    monkeypatch.setenv("MCPCAT_API_KEY", "fixture-network-secret")
    result = await collect(
        runner,
        """import os,sys
print(os.environ.get("MCPCAT_API_KEY", "not-inherited"))
print("fixture-network-", end="", flush=True)
print("secret", file=sys.stdout, flush=True)
print("stderr fixture-network-secret", file=sys.stderr)
""",
    )
    assert "fixture-network-secret" not in json.dumps(result)
    assert "not-inherited" in json.dumps(result) and "[REDACTED]" in json.dumps(result)
    assert runner.running == 0


@pytest.mark.asyncio
async def test_output_limit_and_long_lines():
    runner = NetworkCommandRunner(DiagnosticLogs())
    result = await collect(runner, 'print("x"*50000); print("tail")')
    assert "tail" in json.dumps(result)
    assert "x" * 100 not in json.dumps(result)
    result = await collect(
        runner, 'import time\nwhile True: print("x"*1000, flush=True)'
    )
    assert result[-1]["status"] == "output_limit"
    assert runner.running == 0


@pytest.mark.asyncio
async def test_timeout_and_closed_stream_reap_process(tmp_path):
    runner = NetworkCommandRunner(DiagnosticLogs())
    marker = tmp_path / "process"
    source = f'import os,time; open({str(marker)!r},"w").write(str(os.getpid())); print("ready",flush=True);time.sleep(30)'
    result = await collect(runner, source, timeout=1)
    assert result[-1]["status"] == "timed_out"
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)
    stream = runner.events(command(), [sys.executable, "-u", "-c", source])
    await anext(stream)
    await anext(stream)
    await stream.aclose()
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)
    assert runner.running == 0


@pytest.mark.asyncio
async def test_concurrency_is_bounded_without_mixing_outputs():
    runner = NetworkCommandRunner(DiagnosticLogs())
    streams = [
        runner.events(command(), [sys.executable, "-c", "import time; time.sleep(30)"])
        for _ in range(2)
    ]
    for stream in streams:
        await anext(stream)
    result = await collect(runner, 'print("must-not-start")')
    assert result[0]["status"] == "busy"
    for stream in streams:
        await stream.aclose()
    assert runner.running == 0


@pytest.mark.asyncio
async def test_real_tcp_and_http_and_admin_permissions(content_app):
    app, _ = content_app
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as api:
        for method, path in [
            ("GET", "/api/admin/commands"),
            ("POST", "/api/admin/commands/run"),
        ]:
            body = {"kind": "dns", "target": "localhost"} if method == "POST" else None
            assert (await api.request(method, path, json=body)).status_code == 401
            assert (
                await api.request(method, path, json=body, headers={"Mcpcat-Key": READ})
            ).status_code == 403
        response = await api.post(
            "/api/admin/commands/run",
            json={"kind": "dns", "target": "localhost"},
            headers={"Mcpcat-Key": WRITE},
        )
        assert response.status_code == 200
        assert json.loads(response.text.splitlines()[-1])["exit_code"] == 0
        assert response.headers["cache-control"] == "no-store"
        assert (
            await api.post(
                "/api/admin/commands/run",
                json={"kind": "shell", "target": "id"},
                headers={"Mcpcat-Key": WRITE},
            )
        ).status_code == 422

    async def respond(reader, writer):
        await reader.read(4096)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(respond, "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        runner = NetworkCommandRunner(DiagnosticLogs())
        for request in [
            NetworkCommand(kind="tcp", target="127.0.0.1", port=port),
            NetworkCommand(kind="http", target=f"http://127.0.0.1:{port}/"),
        ]:
            rows = [
                json.loads(item)
                async for item in runner.events(request, command_args(request))
            ]
            assert rows[-1]["exit_code"] == 0
