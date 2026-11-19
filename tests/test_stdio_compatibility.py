"""上游启动准备只修复已确认的工具依赖，不改写配置或共享会话。"""

import asyncio
from copy import deepcopy

import pytest

from app.services.diagnostic_logs import DiagnosticLogs, current_diagnostics
from app.services.proxy_clients import make_proxy_client_factory
from app.services.startup_errors import startup_error_message
from app.services.stdio_compatibility import (
    StartupPreparationError,
    StdioCompatibility,
    mcpdoc_prefix,
)

ERROR = "ModuleNotFoundError: No module named 'mcp.server.fastmcp'"


@pytest.mark.parametrize(
    "command,args,matched",
    [
        (
            "uvx",
            ["--from", "mcpdoc", "mcpdoc", "--urls", "Docs:https://example.test"],
            True,
        ),
        ("/usr/bin/uvx", ["--from", "mcpdoc==0.0.10", "mcpdoc"], True),
        ("uv", ["tool", "run", "mcpdoc"], True),
        ("uvx", ["mcpdoc"], True),
        ("uvx", ["--from", "other-package", "mcpdoc"], False),
        ("uvx", ["--from", "https://example.test/mcpdoc.whl", "mcpdoc"], False),
        ("sh", ["-c", "uvx mcpdoc"], False),
        ("uvx", ["another-mcpdoc"], False),
        ("uvx", ["--from"], False),
    ],
)
def test_only_recognized_package_invocations(command, args, matched):
    assert (mcpdoc_prefix(command, args) is not None) is matched


@pytest.mark.asyncio
async def test_single_preparation_under_concurrency_and_unchanged_config(monkeypatch):
    original = ["--from", "mcpdoc", "mcpdoc", "--urls", "Docs:https://example.test"]
    plan = StdioCompatibility("uvx", original, {}, "docs")
    calls = []

    async def probe(prefix):
        calls.append(list(prefix))
        await asyncio.sleep(0.01)
        return (1, ERROR) if "--with" not in prefix else (0, "")

    monkeypatch.setattr(plan, "_probe", probe)
    store = DiagnosticLogs()
    token = current_diagnostics.set(store)
    try:
        first, second = await asyncio.gather(plan.prepare(), plan.prepare())
    finally:
        current_diagnostics.reset(token)
    assert len(calls) == 2
    assert first == second == ["--from", "mcpdoc", "--with", "mcp<2", *original[2:]]
    assert original == plan.args
    assert any("自动应用" in row["message"] for row in store.query()["items"])


@pytest.mark.asyncio
async def test_future_working_package_is_not_downgraded(monkeypatch):
    plan = StdioCompatibility("uvx", ["mcpdoc"], {}, "docs")

    async def probe(prefix):
        return 0, ""

    monkeypatch.setattr(plan, "_probe", probe)
    assert await plan.prepare() == ["mcpdoc"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args,env,text,expected",
    [
        (["mcpdoc"], {}, "TLS certificate verification failed", "PREPARE_FAILED"),
        (["--with", "mcp>=2", "mcpdoc"], {}, ERROR, "DEPENDENCY_OVERRIDE"),
        (["mcpdoc"], {"UV_CONSTRAINT": "fixture.txt"}, ERROR, "DEPENDENCY_OVERRIDE"),
    ],
)
async def test_unrelated_failures_and_explicit_constraints_not_overridden(
    monkeypatch, args, env, text, expected
):
    plan = StdioCompatibility("uvx", args, env, "docs")
    calls = []

    async def probe(prefix):
        calls.append(prefix)
        return 1, text

    monkeypatch.setattr(plan, "_probe", probe)
    with pytest.raises(StartupPreparationError, match=expected) as error:
        await plan.prepare()
    assert len(calls) == 1
    assert startup_error_message(error.value)
    assert "fixture" not in str(error.value)


@pytest.mark.asyncio
async def test_opt_out_does_not_probe(monkeypatch):
    plan = StdioCompatibility("uvx", ["mcpdoc"], {}, "docs", enabled=False)

    async def forbidden(prefix):
        pytest.fail("compatibility disabled")

    monkeypatch.setattr(plan, "_probe", forbidden)
    assert await plan.prepare() == ["mcpdoc"]


@pytest.mark.asyncio
async def test_real_preparation_process_and_factory_do_not_change_config(tmp_path):
    executable = tmp_path / "uvx"
    executable.write_text("""#!/bin/sh
case "$*" in
  *"--with mcp<2"*) exit 0;;
  *) echo "ModuleNotFoundError: No module named 'mcp.server.fastmcp'" >&2; exit 1;;
esac
""")
    executable.chmod(0o755)
    config = {
        "type": "stdio",
        "command": str(executable),
        "args": ["mcpdoc"],
        "env": {},
    }
    before = deepcopy(config)
    factory = make_proxy_client_factory(config, server_name="docs")
    one, two = factory(), factory()
    first, second = one.transport.transport, two.transport.transport
    assert first is not second
    assert first.compatibility is second.compatibility
    assert await first.compatibility.prepare() == ["--with", "mcp<2", "mcpdoc"]
    assert config == before


@pytest.mark.asyncio
async def test_preparation_timeout_reaps_process_and_does_not_cache_failure(tmp_path):
    import os

    import anyio

    marker = tmp_path / "pid"
    executable = tmp_path / "uvx"
    executable.write_text(f'#!/bin/sh\necho $$ > "{marker}"\nexec sleep 30\n')
    executable.chmod(0o755)
    plan = StdioCompatibility(str(executable), ["mcpdoc"], {}, "docs")
    with pytest.raises(TimeoutError), anyio.fail_after(0.3):
        await plan.prepare()
    if marker.exists():
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)
    assert plan.prepared is None


@pytest.mark.asyncio
async def test_failed_preparation_logs_are_bounded_and_credentials_redacted(tmp_path):
    executable = tmp_path / "uvx"
    executable.write_text("""#!/bin/sh
printf '%s\\n' "$TOKEN"
printf 'certificate verify failed\\n' >&2
printf '%s\\n' "$TOKEN" >&2
exit 1
""")
    executable.chmod(0o755)
    plan = StdioCompatibility(
        str(executable), ["mcpdoc"], {"TOKEN": "fixture-private-token"}, "docs"
    )
    store = DiagnosticLogs()
    token = current_diagnostics.set(store)
    try:
        with pytest.raises(StartupPreparationError):
            await plan.prepare()
    finally:
        current_diagnostics.reset(token)
    assert "fixture-private-token" not in str(store.query())
    assert "certificate verify failed" in str(store.query())
    assert "[REDACTED]" in str(store.query())


@pytest.mark.asyncio
async def test_cancellation_after_process_started_reaps_child(tmp_path):
    import os

    import anyio

    marker = tmp_path / "started"
    executable = tmp_path / "uvx"
    executable.write_text(f'#!/bin/sh\necho $$ > "{marker}"\nexec sleep 30\n')
    executable.chmod(0o755)
    plan = StdioCompatibility(str(executable), ["mcpdoc"], {}, "docs")
    task = asyncio.create_task(plan.prepare())
    try:
        with anyio.fail_after(5):
            while not marker.exists():
                await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)
        assert plan.prepared is None
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
