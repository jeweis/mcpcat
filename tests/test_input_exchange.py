"""原调用持有层：多轮只执行一次，拒绝重答，撤权/超时/关闭清理。"""

import asyncio

import mcp.types as types
import pytest
from mcp.shared.exceptions import MCPError

from app.services.input_exchange import InputExchange, current_exchange


def question():
    return types.ListRootsRequest(method="roots/list")


@pytest.mark.asyncio
async def test_log_routing_buffers_only_original_call_and_checks_access():
    allowed = True
    exchange = InputExchange(lambda: allowed)
    first, second = [], []

    async def first_handler(message):
        first.append(message)

    async def second_handler(message):
        second.append(message)

    exchange.log_handler = first_handler
    await exchange.report_log("first")
    exchange.log_handler = None
    await exchange.report_log("between-requests")
    exchange.log_handler = second_handler
    await exchange.report_log("second")
    allowed = False
    with pytest.raises(MCPError, match="access denied"):
        await exchange.report_log("revoked")
    allowed = True
    await exchange.close()
    await exchange.report_log("closed")
    assert first == ["first"] and second == ["between-requests", "second"]
    assert exchange.log_handler is None and exchange.events.empty()
    assert not exchange.pending_logs and exchange.log_bytes == 0


@pytest.mark.asyncio
async def test_input_log_buffer_is_bounded_and_cleared_on_revoke_close():
    exchange = InputExchange(lambda: True)
    for _ in range(200):
        await exchange.report_log("small")
    with pytest.raises(MCPError, match="buffer limit"):
        await exchange.report_log("overflow")
    assert len(exchange.pending_logs) == 200
    exchange.authorized = lambda: False
    with pytest.raises(MCPError, match="access denied"):
        await exchange.flush_logs()
    await exchange.close()
    assert not exchange.pending_logs and exchange.log_bytes == 0

    oversized = InputExchange(lambda: True)
    with pytest.raises(MCPError, match="buffer limit"):
        await oversized.report_log("x" * 262144)
    assert not oversized.pending_logs and oversized.log_bytes == 0
    await oversized.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["input", "result"])
async def test_response_waits_for_inflight_log_delivery(ending):
    exchange = InputExchange(lambda: True)
    started, release, operation_started = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )

    async def slow_log(message):
        started.set()
        await release.wait()

    async def operation():
        operation_started.set()
        if ending == "input":
            return await exchange.ask(question())
        return "finished"

    exchange.log_handler = slow_log
    sending = asyncio.create_task(exchange.report_log("inflight"))
    try:
        async with asyncio.timeout(3):
            await started.wait()
            exchange.start(operation)
            await operation_started.wait()
            assert exchange.events.empty()
            release.set()
            await sending
            assert (await exchange.events.get())[0] == ending
    finally:
        release.set()
        await asyncio.gather(sending, return_exceptions=True)
        await exchange.close()


@pytest.mark.asyncio
async def test_exchange_retains_one_operation_across_rounds():
    exchange = InputExchange(lambda: True)
    started, finished = [], []

    async def operation():
        started.append(True)
        assert current_exchange.get() is exchange
        first = await exchange.ask(question())
        second = await exchange.ask(question())
        finished.append(True)
        return first.roots + second.roots

    exchange.start(operation)
    try:
        async with asyncio.timeout(5):
            for name in ("one", "two"):
                kind, (key, request) = await exchange.events.get()
                assert kind == "input" and request.method == "roots/list"
                with pytest.raises(MCPError, match="type mismatch"):
                    exchange.respond(key, types.ElicitResult(action="cancel"))
                assert not exchange.answer_future.done()
                exchange.respond(
                    key,
                    types.ListRootsResult(roots=[types.Root(uri=f"file:///{name}")]),
                )
                with pytest.raises(MCPError, match="unavailable"):
                    exchange.respond(key, types.ListRootsResult(roots=[]))
            kind, result = await exchange.events.get()
            assert kind == "result"
            assert [str(root.uri) for root in result] == ["file:///one", "file:///two"]
            assert started == finished == [True]
    finally:
        await exchange.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["revoke", "timeout", "close"])
async def test_exchange_stops_original_wait_and_cleans_up(ending):
    allowed = True
    exchange = InputExchange(lambda: allowed, timeout=0.4 if ending == "timeout" else 5)
    closed = asyncio.Event()

    async def operation():
        try:
            await exchange.ask(question())
            pytest.fail("Unanswered operation must not continue")
        finally:
            closed.set()

    exchange.start(operation)
    try:
        async with asyncio.timeout(5):
            assert (await exchange.events.get())[0] == "input"
            if ending == "close":
                await exchange.close()
            else:
                if ending == "revoke":
                    allowed = False
                kind, error = await exchange.events.get()
                assert kind == "error"
                assert isinstance(
                    error, MCPError if ending == "revoke" else TimeoutError
                )
            await closed.wait()
            assert exchange.answer_future is None
    finally:
        await exchange.close()


@pytest.mark.asyncio
async def test_expiry_does_not_wait_for_abandoned_question_to_be_consumed():
    exchange = InputExchange(lambda: True, timeout=0.1)

    async def operation():
        return await exchange.ask(question())

    exchange.start(operation)
    try:
        await asyncio.wait_for(asyncio.shield(exchange.task), 2)
        kind, error = exchange.events.get_nowait()
        assert kind == "error" and isinstance(error, TimeoutError)
        assert exchange.answer_future is None and exchange.events.empty()
    finally:
        await exchange.close()
