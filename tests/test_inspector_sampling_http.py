"""正式 Inspector 工厂的 HTTP Sampling 工具响应与显式能力声明。"""

import asyncio
import json

import pytest
from fastmcp import Context, FastMCP
from fastmcp_tasks import TasksExtension
from mcp import types
from test_gateway_client_capabilities import live_http
from test_inspector_events import wait_for
from test_proxy_cross_generation import single_era

from app.services.inspector_client_options import InspectorClientOptions
from app.services.inspector_service import InspectorService


def source_for(mode: str, observed: list, executed: list, tasked: bool = False) -> FastMCP:
    """来源只返回 Sampling 协议结果，lookup 用于检测意外执行。"""
    source = FastMCP("manual HTTP sampling")
    if tasked:
        source.add_extension(TasksExtension(url="memory://", name="sampling-http-fixture"))

    @source.tool()
    def lookup() -> str:
        executed.append(True)
        return "unexpected"

    @source.tool(task=tasked)
    async def sample(ctx: Context):
        if not tasked:
            capability = ctx.session.client_capabilities.sampling
            observed.append(capability)
            if capability is None:
                return {"supported": False}
        params = types.CreateMessageRequestParams(
            messages=[types.SamplingMessage(role="user", content=types.TextContent(
                type="text", text="fixture-request"))], max_tokens=20,
            include_context="thisServer",
            tools=None if tasked else [types.Tool(name="lookup", input_schema={"type": "object"})],
            tool_choice=None if tasked else types.ToolChoice(mode="required"),
        )
        if mode == "legacy":
            result = await ctx.session.create_message(
                messages=params.messages, max_tokens=params.max_tokens,
                tools=params.tools, tool_choice=params.tool_choice,
                include_context=params.include_context,
            )
        elif ctx.input_responses is None:
            return types.InputRequiredResult(input_requests={
                "manual": types.CreateMessageRequest(method="sampling/createMessage", params=params),
            })
        else:
            result = ctx.input_responses["manual"]
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    return source


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,tasked,enabled", [
    ("modern", False, True), ("legacy", False, True),
    ("modern", False, False), ("legacy", False, False),
    ("modern", True, True),
])
async def test_formal_http_sampling_tool_result(mode: str, tasked: bool, enabled: bool) -> None:
    """声明、上下文和 tool_use 经真实 HTTP 保留，测试台不执行 lookup。"""
    observed, executed = [], []
    source = source_for(mode, observed, executed, tasked)
    service = InspectorService()
    app = single_era(source.http_app(path="/mcp/demo", json_response=False), mode, [])
    async with asyncio.timeout(20), live_http(app) as endpoint:
        sid = await service.create_session("demo", endpoint.removesuffix("/mcp"),
            client_options=InspectorClientOptions(sampling=enabled))
        session = service.sessions[sid]
        run = session.runs.start(session, "sample", {}, tasked)
        try:
            if enabled:
                await wait_for(lambda: session.runs.snapshot(run["id"])["status"] == "input_required"
                               or session.runs.workers[run["id"]].done())
                assert session.runs.snapshot(run["id"])["status"] == "input_required", session.runs.snapshot(run["id"])
                question = session.runs.snapshot(run["id"])["question"]
                assert question["with_tools"] is (not tasked)
                assert '"includeContext": "thisServer"' in question["message"]
                session.runs.answer(run["id"], question["id"], "accept", {
                    "response_json": json.dumps({
                        "role": "assistant", "model": "manual-test",
                        "stopReason": "endTurn" if tasked else "toolUse",
                        "content": {"type": "text", "text": "manual-task-result"} if tasked else
                        [{"type": "tool_use", "id": "call-1", "name": "lookup", "input": {}}],
                    }),
                })
            await session.runs.workers[run["id"]]
            final = session.runs.snapshot(run["id"])
            assert final["status"] == "completed", final
            assert final["result"]["isError"] is False, final
            assert all((value is not None) == enabled for value in observed)
            assert not executed
            if enabled:
                assert ("manual-task-result" if tasked else "tool_use") in str(final["result"])
                if not tasked:
                    assert observed[0].tools is not None and observed[0].context is not None
            else:
                assert not session.runs.questions
        finally:
            await service.close_session(sid)


@pytest.mark.asyncio
async def test_cancel_task_waiting_for_sampling_clears_question() -> None:
    """取消远程任务而非回答问题后，等待必须退出且旧问题不可再提交。"""
    source = source_for("modern", [], [], True)
    service = InspectorService()
    async with asyncio.timeout(20), live_http(source.http_app(path="/mcp/demo")) as endpoint:
        sid = await service.create_session("demo", endpoint.removesuffix("/mcp"),
            client_options=InspectorClientOptions(sampling=True))
        session = service.sessions[sid]
        run = session.runs.start(session, "sample", {}, True)
        try:
            await wait_for(lambda: session.runs.snapshot(run["id"])["status"] == "input_required")
            question = session.runs.snapshot(run["id"])["question"]
            await session.runs.cancel(run["id"])
            await wait_for(lambda: session.runs.workers[run["id"]].done())
            final = session.runs.snapshot(run["id"])
            assert final["status"] == "cancelled", final
            assert "question" not in final and not session.runs.questions
            with pytest.raises(ValueError, match="失效"):
                session.runs.answer(run["id"], question["id"], "accept", {})
        finally:
            await service.close_session(sid)
