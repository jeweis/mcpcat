import asyncio
from types import SimpleNamespace

import pytest

from app.services.diagnostic_logs import DiagnosticLogs, current_diagnostics
from app.services.mcp_lifecycle import LifecycleHandle, ServiceLifecycles
from app.services.runtime_client import RuntimeProxyClient, failure_detail
from app.services.startup_errors import startup_error_message
from app.services.stdio_compatibility import StartupPreparationError


def test_startup_error_includes_detail_and_marker():
    error = StartupPreparationError("RUNTIME_PROCESS_EXIT", "exit_code=1；stderr=boom")
    text = str(error)
    assert "exit_code=1" in text and "stderr=boom" in text
    assert "[MCPCAT_STARTUP:RUNTIME_PROCESS_EXIT]" in text
    assert startup_error_message(error)


def test_failure_detail_redacts_secret():
    store = DiagnosticLogs()
    store.protect({"api_key": "fixture-secret"})
    token = current_diagnostics.set(store)
    try:
        detail = failure_detail(
            SimpleNamespace(exit_code=1, stderr="boom fixture-secret")
        )
    finally:
        current_diagnostics.reset(token)
    assert detail is not None
    assert "exit_code=1" in detail
    assert "fixture-secret" not in detail
    assert "[REDACTED]" in detail


def test_recovery_prefers_disabling_offline():
    offline = SimpleNamespace(
        mode="auto",
        runtime_transport=SimpleNamespace(
            offline_injected=True, runtime_environment=None
        ),
    )
    attempt = SimpleNamespace(
        category="cache",
        stderr="",
        closed_rpc=False,
        discover_closed=False,
        exit_code=None,
    )
    assert RuntimeProxyClient._recovery_action(offline, attempt) == "no_offline"

    plain = SimpleNamespace(
        mode="auto",
        runtime_transport=SimpleNamespace(
            offline_injected=False, runtime_environment=None
        ),
    )
    assert RuntimeProxyClient._recovery_action(plain, attempt) is None


@pytest.mark.asyncio
async def test_stop_clears_post_start_failure_for_restart():
    name, app = "fixture", object()
    info = {"mcp_app": app}
    manager = SimpleNamespace(
        server_info={name: info},
        lifespan_tasks={name: None},
        dynamic_tasks=set(),
        _update_server_status=lambda *args, **kwargs: None,
    )
    lifecycles = ServiceLifecycles(manager)
    ready = asyncio.get_running_loop().create_future()
    ready.set_result(True)
    handle = LifecycleHandle(info, app, ready)
    handle.failed = True
    handle.started = True
    handle.task = asyncio.create_task(asyncio.sleep(0))
    await handle.task
    lifecycles.handles[name] = handle

    assert await lifecycles.stop(name) is True
    assert name not in lifecycles.handles
