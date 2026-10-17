"""真实 Monty 计算、有限桥接、取消、部分诊断及跨 worker 配额。"""

import asyncio
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool
from test_catalog import server_info

from app.models.code_mode_config import CodeModeConfig
from app.models.mcp_config import CatalogConfig
from app.services.code_mode_execution import SourceGeneration
from app.services.code_mode_leases import ExecutionLeasePool
from app.services.code_mode_provider import CodeModeProvider
from app.services.code_mode_sandbox import CodeModeSandboxProvider

PRINCIPAL = "a" * 64


def sandbox(tmp_path, **limits):
    mcp = FastMCP("math")
    mcp.add_tool(Tool.from_function(lambda value: value, name="value"))
    manager = SimpleNamespace(server_info={"math": server_info(mcp)})
    state = {
        "principal": PRINCIPAL,
        "config": CodeModeConfig(
            enabled=True,
            limits={"sandbox_seconds": 1.0, "deadline_seconds": 3.0, **limits},
        ),
    }
    directory = CodeModeProvider(
        manager,
        authorize=lambda: state["principal"] is not None,
        config_loader=lambda: state["config"],
        catalog_loader=CatalogConfig,
    )
    audit = []
    bridge = CodeModeSandboxProvider(
        directory=directory,
        config_loader=lambda: state["config"],
        principal=lambda: state["principal"],
        sources=lambda: {
            name: SourceGeneration(name, info["mcp"])
            for name, info in manager.server_info.items()
        },
        leases=ExecutionLeasePool(tmp_path / "test.db"),
        audit=audit.append,
    )
    return bridge, state, manager, audit


async def value(name, params):
    return {"value": params["value"]}


@pytest.mark.asyncio
async def test_actual_monty_composition_and_safe_audit(tmp_path):
    bridge, _, _, audit = sandbox(tmp_path)
    code = 'a = await call_tool("math__value", {"value": 2})\nb = await call_tool("math__value", {"value": 3})\nreturn a["value"] + b["value"]'
    assert await bridge.run(code, external_functions={"call_tool": value}) == 5
    assert audit[0]["completed"] == 2
    assert audit[0]["attempted"] == 2
    assert audit[0]["outcome"] == "success"
    assert all(call["source"] == "math" for call in audit[0]["calls"])
    assert "call_tool(" not in json.dumps(audit)
    assert "params" not in json.dumps(audit)


@pytest.mark.asyncio
async def test_hidden_tool_denied_before_dispatch_and_counted(tmp_path):
    bridge, _, _, audit = sandbox(tmp_path)
    called = []

    async def callback(name, params):
        called.append(name)

    with pytest.raises(ToolError, match="tool_denied"):
        await bridge.run(
            'return await call_tool("secret-guessed", {})',
            external_functions={"call_tool": callback},
        )
    assert called == []
    assert audit[0]["attempted"] == 1
    assert "secret-guessed" not in json.dumps(audit)


@pytest.mark.asyncio
async def test_key_revocation_partial_diagnostic_never_retries_or_logs_payload(
    tmp_path,
):
    bridge, state, _, audit = sandbox(tmp_path)
    called = []

    async def revoke(name, params):
        called.append(params)
        if len(called) == 2:
            state["principal"] = None
        return "upstream-private-result"

    code = 'await call_tool("math__value", {"value": "private-parameter"})\nawait call_tool("math__value", {"value": 2})\nreturn 1'
    with pytest.raises(ToolError, match="authorization_changed") as error:
        await bridge.run(code, external_functions={"call_tool": revoke})
    assert len(called) == 2
    assert "not rolled back" in str(error.value)
    assert audit[0]["completed"] == 2
    assert "private-parameter" not in json.dumps(audit)
    assert "upstream-private-result" not in json.dumps(audit)


@pytest.mark.asyncio
async def test_call_limit_remains_stopped_when_code_catches_error(tmp_path):
    bridge, _, _, audit = sandbox(tmp_path, max_tool_calls=1)
    code = 'await call_tool("math__value", {"value": 1})\ntry:\n    await call_tool("math__value", {"value": 2})\nexcept Exception:\n    pass\nreturn "pretend-success"'
    with pytest.raises(ToolError, match="call_limit"):
        await bridge.run(code, external_functions={"call_tool": value})
    assert audit[0]["completed"] == 1
    assert audit[0]["attempted"] == 2


@pytest.mark.asyncio
async def test_tool_timeout_cancels_wait_and_releases_identity_slot(tmp_path):
    bridge, _, _, audit = sandbox(
        tmp_path, tool_timeout_seconds=0.05, principal_concurrency=1
    )
    cancelled = asyncio.Event()

    async def slow(name, params):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with pytest.raises(ToolError, match="tool_timeout"):
        await bridge.run(
            'return await call_tool("math__value", {})',
            external_functions={"call_tool": slow},
        )
    assert cancelled.is_set()
    assert await bridge.run("return 3", external_functions={"call_tool": value}) == 3
    assert not bridge.active
    assert not bridge.retained


@pytest.mark.asyncio
async def test_total_deadline_includes_tool_wait(tmp_path):
    bridge, _, _, _ = sandbox(tmp_path, deadline_seconds=0.05, tool_timeout_seconds=1.0)

    async def slow(name, params):
        await asyncio.Event().wait()

    with pytest.raises(ToolError, match="deadline"):
        await bridge.run(
            'return await call_tool("math__value", {})',
            external_functions={"call_tool": slow},
        )


@pytest.mark.asyncio
async def test_execute_slots_isolate_identities_and_client_cancel_cleans_up(tmp_path):
    bridge, state, _, audit = sandbox(tmp_path, principal_concurrency=1)
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    async def wait(name, params):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    pending = asyncio.create_task(
        bridge.run(
            'return await call_tool("math__value", {})',
            external_functions={"call_tool": wait},
        )
    )
    await asyncio.wait_for(entered.wait(), 3)
    with pytest.raises(ToolError, match="concurrency"):
        await bridge.run("return 1", external_functions={"call_tool": value})
    state["principal"] = "b" * 64
    assert await bridge.run("return 2", external_functions={"call_tool": value}) == 2
    state["principal"] = PRINCIPAL
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert cleaned.is_set()
    assert await bridge.run("return 1", external_functions={"call_tool": value}) == 1
    assert any(event["outcome"] == "cancelled" for event in audit)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        'import os\nreturn os.listdir("/")',
        'return open("/etc/passwd").read()',
        'import subprocess\nreturn subprocess.run(["echo", "no"])',
        "import socket\nreturn socket.socket()",
    ],
)
async def test_sandbox_has_no_host_file_shell_network_access(tmp_path, code):
    bridge, _, _, audit = sandbox(tmp_path)
    with pytest.raises(ToolError, match="sandbox_error"):
        await bridge.run(code, external_functions={"call_tool": value})
    assert audit[0]["attempted"] == 0


@pytest.mark.asyncio
async def test_source_replacement_stops_next_call(tmp_path):
    bridge, _, manager, audit = sandbox(tmp_path)

    async def replace(name, params):
        manager.server_info["math"]["mcp"] = FastMCP("replacement")
        return "done"

    with pytest.raises(ToolError):
        await bridge.run(
            'await call_tool("math__value", {})\nreturn await call_tool("math__value", {})',
            external_functions={"call_tool": replace},
        )
    assert audit[0]["completed"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("all_sources", [False, True])
async def test_callback_can_stop_own_scope_without_deadlock(tmp_path, all_sources):
    bridge, _, _, audit = sandbox(tmp_path)

    async def stop(name, params):
        if all_sources:
            await bridge.cancel_all()
        else:
            await bridge.cancel_source("math")
        return "already completed"

    with pytest.raises(ToolError, match="configuration_changed|source_changed"):
        await asyncio.wait_for(
            bridge.run(
                'return await call_tool("math__value", {})',
                external_functions={"call_tool": stop},
            ),
            timeout=2,
        )
    assert audit[0]["completed"] == 1
    assert not bridge.active
    assert not bridge.retained
    assert await bridge.run("return 1", external_functions={"call_tool": value}) == 1


@pytest.mark.asyncio
async def test_single_completed_call_revalidates_source_before_return(tmp_path):
    bridge, _, manager, audit = sandbox(tmp_path)

    async def replace(name, params):
        manager.server_info["math"]["mcp"] = FastMCP("replacement")
        return "stale data"

    with pytest.raises(ToolError, match="source_changed"):
        await bridge.run(
            'return await call_tool("math__value", {})',
            external_functions={"call_tool": replace},
        )
    assert audit[0]["completed"] == 1


def test_execution_lease_cannot_reenter_and_is_shared_with_other_process(tmp_path):
    path = tmp_path / "test.db"
    pool = ExecutionLeasePool(path)
    lease = pool.acquire(PRINCIPAL, 1)
    try:
        assert ExecutionLeasePool(path).acquire(PRINCIPAL, 1) is None
        script = "from pathlib import Path; from app.services.code_mode_leases import ExecutionLeasePool; import sys; print(ExecutionLeasePool(Path(sys.argv[1])).acquire(sys.argv[2],1) is None)"
        result = subprocess.run(
            [sys.executable, "-c", script, str(path), PRINCIPAL],
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "True"
    finally:
        lease.release()
    next_lease = pool.acquire(PRINCIPAL, 1)
    assert next_lease is not None
    next_lease.release()


def test_lowered_identity_limit_counts_retained_higher_numbered_slots(tmp_path):
    pool = ExecutionLeasePool(tmp_path / "test.db")
    first = pool.acquire(PRINCIPAL, 2)
    second = pool.acquire(PRINCIPAL, 2)
    assert first is not None and second is not None
    first.release()
    try:
        assert pool.acquire(PRINCIPAL, 1) is None
    finally:
        second.release()
    restored = pool.acquire(PRINCIPAL, 1)
    assert restored is not None
    restored.release()


@pytest.mark.asyncio
async def test_actual_monty_parallel_call_limit_prevents_extra_dispatch(tmp_path):
    bridge, _, _, audit = sandbox(tmp_path, tool_concurrency=1)
    calls = []

    async def slow(name, params):
        calls.append(name)
        await asyncio.sleep(0.1)
        return 1

    code = 'import asyncio\nreturn await asyncio.gather(call_tool("math__value", {}), call_tool("math__value", {}))'
    with pytest.raises(ToolError, match="tool_concurrency"):
        await bridge.run(code, external_functions={"call_tool": slow})
    assert len(calls) <= 1
    assert audit[0]["attempted"] == 2


@pytest.mark.asyncio
async def test_invalid_attempts_and_retries_count_without_logging_input(tmp_path):
    bridge, _, _, audit = sandbox(tmp_path)
    code = 'try:\n    await call_tool("bad-secret", secret="private-key")\nexcept Exception:\n    pass\nreturn await call_tool("math__value", {"value": 1})'
    assert await bridge.run(code, external_functions={"call_tool": value}) == {
        "value": 1
    }
    assert audit[0]["attempted"] == 2
    assert audit[0]["calls"][0]["outcome"] == "invalid"
    assert "bad-secret" not in json.dumps(audit)
    assert "private-key" not in json.dumps(audit)


@pytest.mark.asyncio
async def test_compute_and_memory_limits_are_effective_and_finite(tmp_path):
    bridge, _, _, audit = sandbox(
        tmp_path, sandbox_seconds=0.02, memory_bytes=1_000_000
    )
    for code in ["while True:\n    pass", "return [0] * 10000000"]:
        with pytest.raises(ToolError, match="sandbox_error"):
            await bridge.run(code, external_functions={"call_tool": value})
    assert all(event["attempted"] == 0 for event in audit)


@pytest.mark.asyncio
async def test_unknown_extra_callbacks_and_code_size_rejected_before_sandbox(tmp_path):
    bridge, _, _, audit = sandbox(tmp_path, max_code_bytes=20)
    with pytest.raises(ToolError, match="code_size"):
        await bridge.run(
            'return "private-long-code-body"', external_functions={"call_tool": value}
        )
    with pytest.raises(ToolError, match="uncontrolled_callback"):
        await bridge.run(
            "return 1", external_functions={"call_tool": value, "open": open}
        )
    assert [event["reason"] for event in audit] == [
        "code_size",
        "uncontrolled_callback",
    ]
    assert "private-long-code-body" not in json.dumps(audit)


@pytest.mark.asyncio
async def test_unconfirmed_cancel_keeps_quota_until_tool_actually_finishes(tmp_path):
    bridge, _, _, audit = sandbox(
        tmp_path, tool_timeout_seconds=0.03, principal_concurrency=1
    )
    finish = asyncio.Event()

    async def unresponsive(name, params):
        while not finish.is_set():
            try:
                await finish.wait()
            except asyncio.CancelledError:
                pass
        return "finished"

    try:
        with pytest.raises(ToolError, match="tool_timeout"):
            await asyncio.wait_for(
                bridge.run(
                    'return await call_tool("math__value", {})',
                    external_functions={"call_tool": unresponsive},
                ),
                timeout=2,
            )
        assert bridge.retained
        with pytest.raises(ToolError, match="principal_concurrency"):
            await bridge.run("return 1", external_functions={"call_tool": value})
        assert audit[0]["calls"][0]["outcome"] == "cancel_unconfirmed"
    finally:
        finish.set()
        await asyncio.gather(*tuple(bridge.retained), return_exceptions=True)
    assert await bridge.run("return 1", external_functions={"call_tool": value}) == 1
    assert any(event["outcome"] == "cleanup_finished" for event in audit)


@pytest.mark.asyncio
async def test_source_identity_recreation_cannot_reuse_same_runtime_object(tmp_path):
    bridge, _, manager, audit = sandbox(tmp_path)
    source_id = {"value": "before"}
    bridge.sources = lambda: {
        "math": SourceGeneration(source_id["value"], manager.server_info["math"]["mcp"])
    }
    called = []

    async def recreate(name, params):
        called.append(name)
        source_id["value"] = "after"
        return "done"

    with pytest.raises(ToolError, match="source_changed"):
        await bridge.run(
            'await call_tool("math__value", {})\nreturn await call_tool("math__value", {})',
            external_functions={"call_tool": recreate},
        )
    assert len(called) == 1
    assert audit[0]["completed"] == 1
