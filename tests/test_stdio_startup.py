"""启动互斥、跨 worker 锁、取消回收与客户端隔离回归。"""

import asyncio
import os
import sys
from pathlib import Path

import anyio
import pytest
from fastmcp import Client
from test_gateway_client_capabilities import live_http

from app.core.config import settings
from app.services.diagnostic_stdio import DiagnosticStdioTransport
from app.services.mcp_factory import MCPServerFactory
from app.services.upstream_startup import StartupGate
from scripts.stdio_startup_fixture import events, fake_npx_config, source_config


@pytest.fixture(autouse=True)
def isolated_startup_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "mcpcat_database_path", str(tmp_path / "app.db"))


@pytest.mark.asyncio
async def test_cancelled_waiter_cannot_release_another_connection_lock():
    first, second = StartupGate("npx"), StartupGate("npx")
    await first.acquire()
    try:
        with pytest.raises(TimeoutError), anyio.fail_after(0.12):
            try:
                await second.acquire()
            finally:
                second.release()
        assert first.lock.is_locked
        third = StartupGate("npx", timeout=0.1)
        with pytest.raises(TimeoutError):
            await third.acquire()
    finally:
        first.release()
    await second.acquire()
    second.release()


@pytest.mark.asyncio
async def test_gate_is_shared_between_independent_worker_processes(tmp_path):
    first = StartupGate("npx")
    await first.acquire()
    marker = tmp_path / "worker-acquired"
    code = (
        "import asyncio; from pathlib import Path; "
        "from app.core.config import settings; "
        "from app.services.upstream_startup import StartupGate; "
        f"settings.mcpcat_database_path={settings.mcpcat_database_path!r}; "
        f"gate=StartupGate('npx'); asyncio.run(gate.acquire()); Path({str(marker)!r}).touch(); "
        "gate.release()"
    )
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", code)
    try:
        await asyncio.sleep(0.3)
        assert not marker.exists()
    finally:
        first.release()
        with anyio.fail_after(10):
            await process.wait()
    assert process.returncode == 0 and marker.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_concurrent_cold_connections_keep_roots_and_processes_isolated(
    tmp_path, mode
):
    root = tmp_path / "fixture"
    config = fake_npx_config(root, delay=0.25)
    config["env"]["STARTUP_FIXTURE_BARRIER"] = "1"
    proxy = MCPServerFactory.create_server("cold", config)
    async with live_http(proxy.http_app(path="/mcp")) as url:

        async def use(owner):
            async with Client(
                url, mode=mode, roots=[f"file:///fixture/{owner}"], timeout=20
            ) as client:
                assert [tool.name for tool in await client.list_tools()] == ["identity"]
                result = await client.call_tool("identity", {"pause": 0.8})
                assert result.data["roots"] == [f"file:///fixture/{owner}"]
                return result.data["pid"]

        with anyio.fail_after(50):
            pids = await asyncio.gather(use("alice"), use("bob"))
    assert len(set(pids)) == 2
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    rows = events(root)
    starts = [row["time"] for row in rows if row["event"] == "tool_start"]
    ends = [row["time"] for row in rows if row["event"] == "tool_end"]
    assert len(starts) == len(ends) == 2
    assert max(starts) < min(ends), "锁只能保护启动，不能串行化工具运行"


@pytest.mark.asyncio
async def test_startup_has_own_budget_but_normal_requests_still_time_out(tmp_path):
    config = source_config(tmp_path / "slow", delay=0.35)
    transport = DiagnosticStdioTransport(
        command=config["command"],
        args=config["args"],
        env=config["env"],
        keep_alive=False,
        startup_timeout=5,
        request_timeout=0.1,
    )
    async with Client(
        transport, timeout=0.1, init_timeout=5, roots=["file:///fixture/a"]
    ) as client:
        assert [tool.name for tool in await client.list_tools()] == ["identity"]
        with pytest.raises(Exception):
            await client.call_tool("identity", {"pause": 0.5})


@pytest.mark.asyncio
async def test_failed_source_is_an_error_instead_of_empty_tools(tmp_path):
    config = source_config(tmp_path / "failed")
    config["args"] = ["-c", "raise RuntimeError('fixture upstream failed')"]
    proxy = MCPServerFactory.create_server("failed", config)
    with pytest.raises(Exception):
        async with Client(proxy, timeout=5) as client:
            await client.list_tools()


@pytest.mark.asyncio
async def test_initialization_timeout_releases_gate_and_child_process(tmp_path):
    root = tmp_path / "timeout"
    config = fake_npx_config(root, delay=5)
    transport = DiagnosticStdioTransport(
        command=config["command"],
        args=[],
        env=config["env"],
        keep_alive=False,
        startup_timeout=0.7,
    )
    with pytest.raises(Exception), anyio.fail_after(10):
        async with Client(transport, init_timeout=0.8):
            pytest.fail("delayed source must not initialize within the test deadline")
    gate = StartupGate("npx", timeout=0.2)
    await gate.acquire()
    gate.release()
    for row in events(root):
        with pytest.raises(ProcessLookupError):
            os.kill(row["pid"], 0)
