"""问题已经入队后，响应结束仍必须等待同时进入的日志投递。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import mcp.types as types
import anyio
import pytest
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from app.services.input_exchange import InputExchange
from app.services.modern_input_bridge import ModernInputBridge, PendingInput


@pytest.mark.asyncio
async def test_input_response_does_not_close_while_late_log_is_sending(monkeypatch):
    exchange = InputExchange(lambda: True)
    consumed, allow_response, sending, release_log = [asyncio.Event() for _ in range(4)]

    class ControlledQueue(asyncio.Queue):
        async def get(self):
            value = await super().get()
            consumed.set()
            await allow_response.wait()
            return value

    async def log(message):
        sending.set()
        await release_log.wait()

    exchange.events = ControlledQueue(maxsize=1)
    exchange.log_handler = log
    bridge = ModernInputBridge()
    bridge.started = True
    monkeypatch.setattr(bridge, "permitted", lambda: True)
    monkeypatch.setattr(
        bridge,
        "acquire",
        lambda *args: ("fixture", PendingInput("owner", "call", exchange)),
    )
    context = SimpleNamespace(
        fastmcp_context=SimpleNamespace(
            request_context=SimpleNamespace(
                protocol_version=next(iter(MODERN_PROTOCOL_VERSIONS))
            ),
            request_state=None,
        )
    )
    response = asyncio.create_task(
        bridge.run(context, AsyncMock(), lambda value: value)
    )
    delivery = None
    try:
        async with asyncio.timeout(3):
            await exchange.events.put(
                ("input", ("question", types.ListRootsRequest(method="roots/list")))
            )
            await consumed.wait()
            delivery = asyncio.create_task(exchange.report_log("late"))
            await sending.wait()
            allow_response.set()
            done, _ = await asyncio.wait({response}, timeout=0.05)
            assert (
                not done
            ), "Input response closed before its in-flight log was delivered"
            release_log.set()
            await delivery
            assert isinstance(await response, types.InputRequiredResult)
            assert exchange.log_handler is None
    finally:
        allow_response.set()
        release_log.set()
        await asyncio.gather(
            response, *([delivery] if delivery else []), return_exceptions=True
        )
        await exchange.close()


@pytest.mark.asyncio
async def test_cancelled_request_still_detaches_and_closes_exchange(monkeypatch):
    exchange = InputExchange(lambda: True)
    waiting = asyncio.Event()

    class WaitingQueue(asyncio.Queue):
        async def get(self):
            waiting.set()
            return await super().get()

    exchange.events = WaitingQueue()
    bridge = ModernInputBridge()
    bridge.started = True
    monkeypatch.setattr(bridge, "permitted", lambda: True)
    monkeypatch.setattr(
        bridge,
        "acquire",
        lambda *args: ("fixture", PendingInput("owner", "call", exchange)),
    )
    context = SimpleNamespace(
        fastmcp_context=SimpleNamespace(
            request_context=SimpleNamespace(
                protocol_version=next(iter(MODERN_PROTOCOL_VERSIONS))
            ),
            request_state=None,
        )
    )
    scope_ready = asyncio.get_running_loop().create_future()

    async def request():
        with anyio.CancelScope() as scope:
            scope_ready.set_result(scope)
            await bridge.run(context, AsyncMock(), lambda value: value)

    task = asyncio.create_task(request())
    try:
        async with asyncio.timeout(3):
            scope = await scope_ready
            await waiting.wait()
            scope.cancel()
            await task
            assert exchange.closed
            assert exchange.log_handler is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await exchange.close()
