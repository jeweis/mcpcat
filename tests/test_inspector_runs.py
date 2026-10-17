"""测试台异步调用与输入生命周期，不使用生产数据。"""

import asyncio
from types import SimpleNamespace

import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp_tasks import TasksExtension
from mcp_types import ElicitRequestFormParams, ElicitRequest, InputRequiredResult

from app.services.inspector_runs import InspectorRuns
from app.services.inspector_service import InspectorSession


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,tasked", [("auto", False), ("legacy", False), ("auto", True)]
)
async def test_run_real_sdk_elicitation_and_result(mode, tasked):
    server = FastMCP("interactive fixture")
    if tasked:
        server.add_extension(TasksExtension(url="memory://", name="input-fixture"))

    @server.tool(task=tasked)
    async def ask(ctx: Context):
        if mode == "auto":
            if ctx.input_responses is None:
                return InputRequiredResult(
                    input_requests={
                        "city": ElicitRequest(
                            method="elicitation/create",
                            params=ElicitRequestFormParams(
                                message="输入城市",
                                requested_schema={
                                    "type": "object",
                                    "properties": {"value": {"type": "string"}},
                                    "required": ["value"],
                                },
                            ),
                        )
                    }
                )
            answer = ctx.input_responses["city"]
            return answer.content["value"] if answer.action == "accept" else "declined"
        response = await ctx.elicit("输入城市", str)
        return str(response.data) if response.action == "accept" else "declined"

    runs = InspectorRuns()
    client = Client(server, mode=mode, elicitation_handler=runs.elicit)
    session = InspectorSession("fixture", client)
    session.runs = runs
    run = runs.start(session, "ask", {}, tasked)
    async with asyncio.timeout(5):
        while runs.snapshot(run["id"])["status"] != "input_required":
            assert not runs.workers[run["id"]].done(), runs.snapshot(run["id"])
            await asyncio.sleep(0.01)
    state = runs.snapshot(run["id"])
    with pytest.raises(ValueError):
        runs.answer(run["id"], "stale", "accept", {"value": "杭州"})
    with pytest.raises(ValueError):
        runs.answer(run["id"], state["question"]["id"], "accept", {"value": 7})
    runs.answer(run["id"], state["question"]["id"], "accept", {"value": "杭州"})
    await runs.workers[run["id"]]
    final = runs.snapshot(run["id"])
    assert final["status"] == "completed", final
    assert "杭州" in str(final["result"])
    assert "杭州" not in str(session.records)
    assert "question" not in final
    await runs.close()
    assert not runs.runs


@pytest.mark.asyncio
async def test_run_close_cancels_waiters_and_rejects_double_start():
    server = FastMCP("slow fixture")

    @server.tool()
    async def slow() -> str:
        await asyncio.sleep(30)
        return "done"

    session = InspectorSession("fixture", Client(server))
    runs = session.runs
    run = runs.start(session, "slow", {}, False)
    with pytest.raises(ValueError):
        runs.start(session, "slow", {}, False)
    await asyncio.sleep(0.05)
    await runs.cancel(run["id"])
    assert runs.snapshot(run["id"])["status"] == "unconfirmed"
    await runs.close()
    assert not runs.workers


@pytest.mark.asyncio
async def test_unsupported_tasks_never_execute_tool():
    server = FastMCP("no task capability")
    calls = []

    @server.tool()
    def write() -> str:
        calls.append(1)
        return "done"

    session = InspectorSession("fixture", Client(server))
    run = session.runs.start(session, "write", {}, True)
    await session.runs.workers[run["id"]]
    assert session.runs.snapshot(run["id"])["status"] == "failed"
    assert calls == []


@pytest.mark.asyncio
async def test_url_and_external_schema_are_not_followed():
    runs = InspectorRuns()
    runs.active_id = "fixture"
    runs.runs["fixture"] = {}
    url = SimpleNamespace(mode="url")
    assert (await runs.elicit("open", None, url, None)).action == "cancel"
    params = ElicitRequestFormParams(
        message="external",
        requested_schema={"type": "object", "$ref": "https://invalid.test/schema"},
    )
    assert (await runs.elicit("external", None, params, None)).action == "cancel"
    assert not runs.questions


@pytest.mark.asyncio
async def test_real_protocol_task_has_handle_and_result():
    server = FastMCP("task fixture")
    server.add_extension(TasksExtension(url="memory://", name="inspector-test"))

    @server.tool(task=True)
    async def calculate() -> dict:
        await asyncio.sleep(0.05)
        return {"total": 42}

    session = InspectorSession("fixture", Client(server, mode="auto"))
    run = session.runs.start(session, "calculate", {}, True)
    async with asyncio.timeout(15):
        await session.runs.workers[run["id"]]
    final = session.runs.snapshot(run["id"])
    assert final["kind"] == "task"
    assert final["status"] == "completed", final
    assert final["result"]["structuredContent"] == {"total": 42}
    await session.runs.close()


@pytest.mark.asyncio
async def test_protocol_task_cancel_reports_remote_terminal_status():
    server = FastMCP("cancel task fixture")
    server.add_extension(TasksExtension(url="memory://", name="cancel-fixture"))

    @server.tool(task=True)
    async def slow() -> str:
        await asyncio.sleep(30)
        return "done"

    session = InspectorSession("fixture", Client(server))
    run = session.runs.start(session, "slow", {}, True)
    async with asyncio.timeout(15):
        while run["id"] not in session.runs.handles:
            assert not session.runs.workers[run["id"]].done()
            await asyncio.sleep(0.01)
        await session.runs.cancel(run["id"])
        await session.runs.workers[run["id"]]
    assert session.runs.snapshot(run["id"])["status"] == "cancelled"
    await session.runs.close()
