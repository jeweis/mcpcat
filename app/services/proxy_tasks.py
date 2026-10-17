"""上游 Tasks 代理适配；HTTP 来源装配复用当前身份与服务代际检查。"""

from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from hashlib import sha256
from time import monotonic
from typing import Any
from uuid import uuid4

from fastmcp.server.dependencies import get_http_request
from fastmcp.server.extensions import MethodBinding
from fastmcp.tools.base import InputRequiredToolResult, ToolResult
from fastmcp_tasks import TasksExtension
from fastmcp_tasks.client import TasksClientExtension
from fastmcp_tasks.client_models import (
    CancelTaskRequest,
    ClientCreateTaskResult,
    ClientGetTaskResult,
    GetTaskRequest,
    UpdateTaskRequest,
)
from fastmcp_tasks.models import (
    MISSING_REQUIRED_CLIENT_CAPABILITY,
    CancelTaskParams,
    CancelTaskResult,
    CreateTaskResult,
    GetTaskParams,
    UpdateTaskParams,
    UpdateTaskResult,
)
from mcp.shared.exceptions import MCPError
from mcp.shared.inbound import MCP_NAME_HEADER, decode_header_value
from mcp.types import INVALID_REQUEST, InputRequiredResult
from mcp_types.jsonrpc import HEADER_MISMATCH
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from app.services.proxy_task_context import (
    opted_proxy_tool,
    requested_tool,
    task_dispatch,
)
from app.services.proxy_task_submission import submit_task_call
from app.services.proxy_task_connection import TaskConnection
from app.services.proxy_task_pool import TaskConnectionPool


@dataclass(frozen=True)
class TaskBinding:
    """仅存路由身份，不保存工具参数、结果或平台凭证。"""

    owner: str
    upstream_id: str
    expires: float
    connection: TaskConnection | None = None


class ProxyTasks(TasksExtension):
    """复用官方扩展生命周期和模型，工作仍由远端任务服务执行。"""

    def __init__(
        self,
        factory: Callable,
        principal: Callable,
        authorized: Callable,
        *,
        retain_connection: bool = False,
    ):
        super().__init__(url="memory://", name="mcpcat-relay-" + uuid4().hex)
        self.factory, self.principal, self.authorized = factory, principal, authorized
        self.bindings: dict[str, TaskBinding] = {}
        self.pending = 0
        self.alive = False
        self.pool = (
            TaskConnectionPool(factory, self.owner) if retain_connection else None
        )

    @asynccontextmanager
    async def lifespan(self):
        """停服丢弃本代路由，不把关闭网关误当成取消上游业务任务。"""
        async with super().lifespan():
            self.alive = True
            try:
                yield
            finally:
                self.alive = False
                if self.pool is not None:
                    await self.pool.close()
                self.bindings.clear()

    def methods(self) -> Sequence[MethodBinding]:
        """公开方法绑定由框架负责解析请求和版本约束。"""
        return [
            MethodBinding(
                method=name,
                params_type=model,
                handler=handler,
                protocol_versions=frozenset(MODERN_PROTOCOL_VERSIONS),
            )
            for name, model, handler in (
                ("tasks/get", GetTaskParams, self.get),
                ("tasks/update", UpdateTaskParams, self.update),
                ("tasks/cancel", CancelTaskParams, self.cancel),
            )
        ]

    def owner(self) -> str:
        """每次操作复核当前身份，未授权不能降级匿名。"""
        if not self.alive or not self.authorized():
            raise MCPError(INVALID_REQUEST, "Task access denied")
        return sha256(self.principal().encode()).hexdigest()

    def lookup(self, ctx: Any, task_id: str) -> TaskBinding:
        """统一隐藏未知、其他身份和过期任务；检查逐请求能力及路由头。"""
        if self.client_settings(ctx) is None:
            raise MCPError(
                MISSING_REQUIRED_CLIENT_CAPABILITY, "Tasks capability required"
            )
        try:
            header = get_http_request().headers.get(MCP_NAME_HEADER)
        except RuntimeError:
            header = None
        if header is not None and decode_header_value(header) != task_id:
            raise MCPError(HEADER_MISMATCH, "Task routing header mismatch")
        owner = self.owner()
        binding = self.bindings.get(task_id)
        if binding is None or binding.owner != owner or binding.expires <= monotonic():
            raise MCPError(INVALID_REQUEST, "Task unavailable")
        return binding

    async def intercept_tool_call(
        self, params: Any, context: Any, call_next: Callable
    ) -> Any:
        """只有真实客户端直接命中的调用可以继承本次 Tasks 协商。"""
        with task_dispatch() as outermost:
            if not outermost or not await requested_tool(params.name):
                return await call_next()
            return await self.dispatch(params, context, call_next)

    async def dispatch(self, params: Any, context: Any, call_next: Callable) -> Any:
        """仅对显式协商的现代请求代理任务，不重放或降级副作用调用。"""
        if not await opted_proxy_tool(params, context, self.identifier):
            return await call_next()
        owner = self.owner()

        now = monotonic()
        self.bindings = {
            key: value for key, value in self.bindings.items() if value.expires > now
        }
        if len(self.bindings) + self.pending >= 128:
            raise MCPError(INVALID_REQUEST, "Task relay limit exceeded")
        self.pending += 1
        connection = None
        try:
            if self.pool is not None:
                connection = await self.pool.open(owner)
            factory = (
                self.factory
                if connection is None
                else lambda **options: connection.use()
            )
            raw = await submit_task_call(factory, self.owner, owner, params, context)
            if self.owner() != owner:
                raise MCPError(
                    INVALID_REQUEST, "Task identity changed; outcome unconfirmed"
                )
            result = self.map_result(raw, owner, connection)
            if isinstance(raw, ClientCreateTaskResult):
                connection = None
            return result
        finally:
            self.pending -= 1
            if connection is not None:
                await self.pool.release(connection)

    def map_result(
        self, raw: Any, owner: str, connection: TaskConnection | None = None
    ) -> Any:
        """只有真实 task 结果分配路由，普通结果与输入请求保留原语义。"""
        if isinstance(raw, ClientCreateTaskResult):
            alias = "mcpcat-task-" + uuid4().hex
            ttl = min(raw.ttl_ms if raw.ttl_ms is not None else 3600000, 3600000)
            self.bindings[alias] = TaskBinding(
                owner, raw.task_id, monotonic() + max(0, ttl) / 1000, connection
            )
            if connection is not None:
                connection.expires = self.bindings[alias].expires
            fields = raw.model_dump()
            fields.update(task_id=alias, ttl_ms=ttl)
            return CreateTaskResult.model_validate(fields)
        if isinstance(raw, InputRequiredResult):
            return InputRequiredToolResult(raw)
        return ToolResult(
            content=raw.content,
            structured_content=raw.structured_content,
            meta=raw.meta,
            is_error=raw.is_error,
        )

    async def forward(self, ctx: Any, params: Any, request: Any, result: Any) -> Any:
        """每跳使用配置凭证；查询/回答/取消均绑定原始任务和当前身份。"""
        binding = self.lookup(ctx, params.task_id)
        upstream = params.model_copy(update={"task_id": binding.upstream_id})
        transport = (
            binding.connection.use()
            if binding.connection is not None
            else self.factory(extensions=[TasksClientExtension()])
        )
        async with transport as client:
            self.lookup(ctx, params.task_id)
            envelope = request.model_validate(
                {"params": upstream.model_dump(by_alias=True)}
            )
            value = await client.session.send_request(envelope, result)
        self.lookup(ctx, params.task_id)
        return value

    async def get(self, ctx: Any, params: GetTaskParams) -> Any:
        """返回真实远端状态、结果或输入请求，替换且仅替换路由任务 ID。"""
        value = await self.forward(ctx, params, GetTaskRequest, ClientGetTaskResult)
        return value.model_copy(update={"task_id": params.task_id})

    async def update(self, ctx: Any, params: UpdateTaskParams) -> Any:
        """将用户回答送到原任务，不在网关自动回答。"""
        return await self.forward(ctx, params, UpdateTaskRequest, UpdateTaskResult)

    async def cancel(self, ctx: Any, params: CancelTaskParams) -> Any:
        """转发合作式取消，不承诺事务回滚，也不删除尚需查询的任务。"""
        return await self.forward(ctx, params, CancelTaskRequest, CancelTaskResult)
