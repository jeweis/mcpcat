"""Tasks 公开扩展代理探针；不以独立探针代替生产管理器装配验收。"""

import asyncio

import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.client.elicitation import ElicitResult
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.providers.proxy import FastMCPProxy
from fastmcp_tasks import TasksExtension, call_tool_task
from fastmcp_tasks.client_models import (
    CancelTaskRequest,
    CancelTaskRequestParams,
    ClientGetTaskResult,
    GetTaskRequest,
    GetTaskRequestParams,
    UpdateTaskRequest,
    UpdateTaskRequestParams,
)
from fastmcp_tasks.models import CancelTaskResult, UpdateTaskResult
from mcp.shared.exceptions import MCPError
from mcp_types import ElicitRequest, ElicitRequestFormParams, InputRequiredResult
from test_gateway_client_capabilities import live_http

from app.services.proxy_clients import make_proxy_client_factory
from app.services.proxy_tasks import ProxyTasks


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "cancelled", "input"])
async def test_tasks_relay_result_and_owner_isolation(outcome):
    source = FastMCP("task source")
    source.add_extension(TasksExtension(url="memory://", name="relay-fixture"))
    calls = []
    allowed = {"alice", "bob"}

    @source.tool(task=True)
    async def compute(value: str, ctx: Context):
        if outcome == "input" and ctx.input_responses is None:
            return InputRequiredResult(
                input_requests={
                    "confirm": ElicitRequest(
                        method="elicitation/create",
                        params=ElicitRequestFormParams(
                            message="Confirm fixture",
                            requested_schema={
                                "type": "object",
                                "properties": {"value": {"type": "string"}},
                                "required": ["value"],
                            },
                        ),
                    )
                }
            )
        if outcome == "input":
            assert ctx.input_responses["confirm"].content == {"value": "approved"}
        calls.append(value)
        if outcome == "cancelled":
            await asyncio.sleep(30)
        return "result:" + value

    questions = []

    async def answer(message, response_type, params, context):
        questions.append(message)
        return ElicitResult(action="accept", content={"value": "approved"})

    async with asyncio.timeout(30), live_http(source.http_app(path="/mcp")) as upstream:
        factory = make_proxy_client_factory(
            {"type": "streamable-http", "url": upstream}
        )
        gateway = FastMCPProxy(client_factory=factory)
        adapter = ProxyTasks(
            factory,
            lambda: get_http_request().headers["x-test-owner"],
            lambda: get_http_request().headers["x-test-owner"] in allowed,
        )
        gateway.add_extension(adapter)
        async with live_http(gateway.http_app(path="/mcp")) as url:
            async with Client(
                StreamableHttpTransport(url, headers={"x-test-owner": "alice"}),
                elicitation_handler=answer,
            ) as alice:
                handle = await call_tool_task(alice, "compute", {"value": "one"})
                assert handle.task_id.startswith("mcpcat-task-")
                async with Client(
                    StreamableHttpTransport(url, headers={"x-test-owner": "bob"})
                ) as bob:
                    requests = [
                        (
                            GetTaskRequest(
                                params=GetTaskRequestParams(task_id=handle.task_id)
                            ),
                            ClientGetTaskResult,
                        ),
                        (
                            CancelTaskRequest(
                                params=CancelTaskRequestParams(task_id=handle.task_id)
                            ),
                            CancelTaskResult,
                        ),
                        (
                            UpdateTaskRequest(
                                params=UpdateTaskRequestParams(
                                    task_id=handle.task_id, input_responses={}
                                )
                            ),
                            UpdateTaskResult,
                        ),
                    ]
                    for request, response in requests:
                        with pytest.raises(MCPError, match="Task unavailable"):
                            await bob.session.send_request(request, response)
                if outcome == "cancelled":
                    await handle.cancel()
                    assert (
                        await handle.wait(state="cancelled", timeout=5)
                    ).status == "cancelled"
                else:
                    result = await handle.result()
                    assert "result:one" in str(result)
                    assert (await handle.status()).status == "completed"
                allowed.remove("alice")
                with pytest.raises(MCPError, match="Task access denied"):
                    await handle.status()
                with pytest.raises(MCPError, match="Task access denied"):
                    await handle.cancel()
                if outcome != "cancelled":
                    assert calls == ["one"]
                assert questions == (["Confirm fixture"] if outcome == "input" else [])
        assert adapter.bindings == {}
        assert adapter.alive is False
