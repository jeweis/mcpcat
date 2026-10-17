"""资源及提示读取共享交互运行生命周期，但保留原协议结果结构。"""

import asyncio

import pytest
from fastmcp import Client, Context, FastMCP

from app.services.inspector_service import InspectorSession


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,name",
    [
        ("resources", "fixture://read"),
        ("templates", "fixture://{name}"),
        ("prompts", "welcome"),
    ],
)
async def test_interactive_read_result(operation: str, name: str) -> None:
    server = FastMCP("read fixture")

    async def ask(ctx: Context) -> str:
        result = await ctx.elicit("请输入演示名称", str)
        return str(result.data) if result.action == "accept" else "declined"

    @server.resource("fixture://read")
    async def resource(ctx: Context) -> str:
        return await ask(ctx)

    @server.resource("fixture://{name}")
    async def template(name: str, ctx: Context) -> str:
        return name + ":" + await ask(ctx)

    @server.prompt()
    async def welcome(ctx: Context) -> str:
        return await ask(ctx)

    session = InspectorSession("fixture", Client(server, mode="legacy"))
    session.client = Client(
        server, mode="legacy", elicitation_handler=session.runs.elicit
    )
    run = session.runs.start(
        session,
        name,
        {"name": "reader"} if operation == "templates" else {},
        False,
        operation,
    )
    try:
        async with asyncio.timeout(5):
            while session.runs.snapshot(run["id"])["status"] != "input_required":
                assert not session.runs.workers[
                    run["id"]
                ].done(), session.runs.snapshot(run["id"])
                await asyncio.sleep(0.01)
        state = session.runs.snapshot(run["id"])
        session.runs.answer(
            run["id"], state["question"]["id"], "accept", {"value": "confirmed"}
        )
        await session.runs.workers[run["id"]]
        result = session.runs.snapshot(run["id"])
        assert result["status"] == "completed", result
        assert "confirmed" in str(result["result"])
        assert ("messages" if operation == "prompts" else "contents") in result[
            "result"
        ]
        assert "isError" not in result["result"]
    finally:
        await session.runs.close()
