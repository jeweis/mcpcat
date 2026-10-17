"""任务查询日志必须属于当前请求，不进入另一身份或订阅流。"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from time import monotonic

import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.dependencies import get_http_request
from fastmcp_tasks import call_tool_task
from fastmcp_tasks.client_models import (
    ClientGetTaskResult,
    GetTaskRequest,
    GetTaskRequestParams,
)
from mcp.client.subscriptions import listen
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler
from mcp.shared.exceptions import MCPError
from mcp.shared.subscriptions import ToolsListChanged
from tests.test_gateway_client_capabilities import live_http
from tests.test_inspector_task_sampling_gateway import InteractiveTaskSource
from tests.test_subscription_public_adapter import PublicListenAdapter

from app.services.mcp_factory import MCPServerFactory
from app.services.proxy_clients import make_proxy_client_factory
from app.services.proxy_subscriptions import ProxySubscriptions
from app.services.proxy_tasks import ProxyTasks


class QuietTransport(StreamableHttpTransport):
    """通过公开会话配置关闭 FastMCP 客户端默认的日志 opt-in。"""

    @asynccontextmanager
    async def connect_session(self, **kwargs):
        kwargs["log_level"] = None
        async with super().connect_session(**kwargs) as session:
            yield session


class LoggingTaskSource(InteractiveTaskSource):
    """独立合规来源直接产生每次 tasks/get 的日志和终态。"""

    async def get_task(self, context, params):
        await context.session.send_log_message(
            level="warning",
            data={"task": params.task_id},
            logger="task-fixture",
            related_request_id=context.request_id,
        )
        from fastmcp_tasks.models import GetTaskResult

        return GetTaskResult(
            **self.fields(),
            status="failed",
            error={
                "code": -32603,
                "message": "fixture failed",
                "data": {"kept": True},
            },
        )


@pytest.mark.asyncio
async def test_task_poll_logs_errors_ttl_and_subscription_isolation():
    bus = InMemorySubscriptionBus()
    source = FastMCP(
        "logging tasks", middleware=[PublicListenAdapter(ListenHandler(bus))]
    )
    extension = LoggingTaskSource()
    source.add_extension(extension)

    @source.tool()
    def sample() -> str:
        raise AssertionError("must not execute tool")

    observed, subscriber_logs = [], []

    async def receive(message):
        observed.append(message.data)

    async def subscriber_log(message):
        subscriber_logs.append(message.data)

    async with asyncio.timeout(25), live_http(source.http_app(path="/mcp")) as upstream:
        config = {"type": "streamable-http", "url": upstream}
        gateway = MCPServerFactory.create_server("logged-tasks", config)
        factory = make_proxy_client_factory(config)
        adapter = ProxyTasks(
            factory, lambda: get_http_request().headers["x-owner"], lambda: True
        )
        gateway.add_extension(adapter)
        gateway.add_middleware(ProxySubscriptions(factory, lambda request: True))
        async with live_http(gateway.http_app(path="/mcp")) as endpoint:

            def transport(owner):
                return QuietTransport(endpoint, headers={"x-owner": owner})

            async with Client(transport("alice"), log_handler=receive) as alice, Client(
                transport("bob"), log_handler=subscriber_log
            ) as bob:
                async with listen(bob.session, tools_list_changed=True) as events:
                    handle = await call_tool_task(alice, "sample", {})
                    params = GetTaskRequestParams(
                        task_id=handle.task_id,
                        meta={"io.modelcontextprotocol/logLevel": "warning"},
                    )
                    result = await alice.session.send_request(
                        GetTaskRequest(params=params), ClientGetTaskResult
                    )
                    assert result.status == "failed" and result.error == {
                        "code": -32603,
                        "message": "fixture failed",
                        "data": {"kept": True},
                    }
                    assert result.task_id == handle.task_id
                    assert observed == [{"task": "source-task"}]
                    # 未 opt-in 的查询不能把来源日志发回客户端。
                    await handle.status()
                    assert observed == [{"task": "source-task"}]
                    await bus.publish(ToolsListChanged())
                    assert isinstance(await anext(events), ToolsListChanged)
                    assert subscriber_logs == []
                    before = extension.calls
                    adapter.bindings[handle.task_id] = replace(
                        adapter.bindings[handle.task_id], expires=monotonic() - 1
                    )
                    with pytest.raises(MCPError, match="Task unavailable"):
                        await handle.status()
                    assert extension.calls == before == 1
            assert adapter.pending == 0
        assert adapter.bindings == {}
