"""合规任务来源经生产网关的工具型 Sampling 与多问题交互。

来源使用公开扩展绑定构造协议夹具，不修改固定 Tasks 执行引擎。
"""

import asyncio
import json
from typing import Any

import pytest
from fastmcp import FastMCP
from fastmcp.server.extensions import MethodBinding
from fastmcp_tasks import TasksExtension
from fastmcp_tasks.models import (
    CancelTaskParams, CancelTaskResult, CreateTaskResult, GetTaskParams,
    GetTaskResult, UpdateTaskParams, UpdateTaskResult,
)
from mcp import types
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
from tests.test_gateway_client_capabilities import live_http
from tests.test_inspector_events import wait_for
from tests.test_skill_content_workflow import READ, content_app

from app.application import create_app
from app.services.config_service import ConfigService
from app.services.inspector_client_options import InspectorClientOptions
from app.services.inspector_service import InspectorService, inspector_service
from app.services.market_service import MarketService
from app.services.server_manager import MCPServerManager


class InteractiveTaskSource(TasksExtension):
    """单任务测试来源：消费协议回答，不运行模型或返回的工具指令。"""

    def __init__(self) -> None:
        super().__init__(url="memory://", name="tool-sampling-source")
        self.calls = 0
        self.received: dict[str, Any] | None = None

    def fields(self) -> dict:
        """固定任务身份只属于本夹具实例。"""
        return dict(task_id="source-task", created_at="2026-10-18T00:00:00Z",
                    last_updated_at="2026-10-18T00:00:00Z", ttl_ms=60000,
                    poll_interval_ms=20)

    def methods(self):
        """通过公开 MethodBinding 保留框架的协议解析与序列化。"""
        return [MethodBinding(method=method, params_type=model, handler=handler,
                              protocol_versions=frozenset(MODERN_PROTOCOL_VERSIONS))
                for method, model, handler in [
                    ("tasks/get", GetTaskParams, self.get_task),
                    ("tasks/update", UpdateTaskParams, self.update_task),
                    ("tasks/cancel", CancelTaskParams, self.cancel_task),
                ]]

    async def intercept_tool_call(self, params, context, call_next):
        """真实 tools/call 只创建一次任务。"""
        self.calls += 1
        return CreateTaskResult(**self.fields(), status="working")

    async def get_task(self, context, params):
        """依次请求模型与目录；后续返回原始回答作为工具结果。"""
        assert params.task_id == "source-task"
        if self.received is None:
            sampling = types.CreateMessageRequest(method="sampling/createMessage",
                params=types.CreateMessageRequestParams(max_tokens=10,
                    messages=[types.SamplingMessage(role="user", content=types.TextContent(
                        type="text", text="synthetic task sampling"))],
                    tools=[types.Tool(name="lookup", input_schema={"type": "object"})]))
            return GetTaskResult(**self.fields(), status="input_required", input_requests={
                "sample": sampling.model_dump(mode="json", by_alias=True),
                "roots": {"method": "roots/list"},
            })
        if "code" in self.received["sample"]:
            return GetTaskResult(**self.fields(), status="failed",
                                 error={"code": -32603, "message": "Sampling declined"})
        return GetTaskResult(**self.fields(), status="completed", result={
            "content": [{"type": "text", "text": json.dumps(self.received)}],
            "isError": False,
        })

    async def update_task(self, context, params):
        """工具型结果按对应 SDK 模型验证，目录声明仅作为数据。"""
        assert params.task_id == "source-task"
        responses = params.input_responses
        assert set(responses) == {"sample", "roots"}
        if "code" not in responses["sample"]:
            types.CreateMessageResultWithTools.model_validate(responses["sample"])
        types.ListRootsResult.model_validate(responses["roots"])
        self.received = responses
        return UpdateTaskResult()

    async def cancel_task(self, context, params):
        """本夹具的主要场景不发取消请求。"""
        return CancelTaskResult()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
async def test_task_sampling_tools_roots_production_gateway(content_app, monkeypatch, action):
    """正式 Inspector→受鉴权网关→真实 HTTP 来源，保留工具型回答与目录。"""
    _, database = content_app
    source = FastMCP("task sampling")
    extension = InteractiveTaskSource()
    source.add_extension(extension)
    executed = []

    @source.tool()
    def sample() -> str:
        executed.append("unexpected")
        return "must be intercepted"

    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    service = InspectorService()
    async with asyncio.timeout(30), live_http(source.http_app(path="/mcp")) as upstream:
        ConfigService.add_server_to_config("task-sampling", {
            "type": "streamable-http", "url": upstream, "require_auth": True,
        })
        manager = MCPServerManager()
        app = create_app(database=database, server_manager=manager)
        async with live_http(app) as endpoint:
            sid = await service.create_session("task-sampling", endpoint.removesuffix("/mcp"),
                api_key=READ, client_options=InspectorClientOptions(
                    sampling=True, roots=[{"uri": "file:///client-fixture"}]))
            session = service.sessions[sid]
            run = session.runs.start(session, "sample", {}, True)
            try:
                await wait_for(lambda: session.runs.snapshot(run["id"])["status"] == "input_required"
                               or session.runs.workers[run["id"]].done())
                state = session.runs.snapshot(run["id"])
                assert state["status"] == "input_required", state
                session.runs.answer(run["id"], state["question"]["id"], action, {
                    "response_json": json.dumps({"role": "assistant", "model": "manual",
                        "stopReason": "toolUse", "content": [
                            {"type": "tool_use", "id": "fixture", "name": "lookup", "input": {}},
                        ]}),
                } if action == "accept" else None)
                await session.runs.workers[run["id"]]
                final = session.runs.snapshot(run["id"])
                assert final["status"] == ("completed" if action == "accept" else "failed"), final
                assert extension.calls == 1 and executed == []
                assert extension.received["roots"]["roots"][0]["uri"] == "file:///client-fixture"
                if action == "accept":
                    assert extension.received["sample"]["content"][0]["type"] == "tool_use"
                assert not session.runs.questions
            finally:
                await service.close_session(sid)
