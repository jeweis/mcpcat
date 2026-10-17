"""单次编排的有限状态与调用边界，不持有代码、参数、结果或凭证。"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import monotonic
from typing import Any
from uuid import uuid4

import anyio
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool

from app.models.code_mode_config import CodeModeConfig
from app.services.code_mode_provider import ORIGIN_META, CodeModeProvider

logger = logging.getLogger("mcpcat.code_mode.audit")
CLEANUP_SECONDS = 0.25


class ExecutionStopped(Exception):
    """仅包含网关自己定义的安全原因，不包装用户异常文本。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason


@dataclass
class CallRecord:
    """只记录已解析的公开工具身份及生命周期。"""

    started: float = field(default_factory=monotonic)
    name: str = "[unresolved]"
    source: str = ""
    outcome: str = "pending"
    duration_ms: int = 0


@dataclass(frozen=True)
class SourceGeneration:
    """同时绑定持久来源身份与当前运行对象，重启/重建均不能串源。"""

    identity: str
    runtime: object = field(repr=False, compare=False)

    def matches(self, other: "SourceGeneration | None") -> bool:
        """不把同名、同 UUID 的新运行对象当成当前执行的来源。"""
        return (
            other is not None
            and self.identity == other.identity
            and self.runtime is other.runtime
        )


EXECUTION_SCOPE: ContextVar["ExecutionScope | None"] = ContextVar(
    "mcpcat_code_execution", default=None
)


def audit_execution(summary: dict) -> None:
    """单行结构化审计，字段由网关构造，绝不记录代码/参数/结果。"""
    logger.info("CodeMode %s", json.dumps(summary, sort_keys=True, ensure_ascii=True))


def parse_tool_call(args: tuple, kwargs: dict) -> tuple[str, dict]:
    """包括无效参数的尝试也先计数，再以固定诊断拒绝。"""
    if len(args) > 2 or set(kwargs) - {"tool_name", "params"}:
        raise ExecutionStopped("invalid_call")
    values = dict(zip(("tool_name", "params"), args))
    if set(values) & set(kwargs):
        raise ExecutionStopped("invalid_call")
    values.update(kwargs)
    if not isinstance(values.get("tool_name"), str) or not isinstance(
        values.get("params"), dict
    ):
        raise ExecutionStopped("invalid_call")
    return values["tool_name"], values["params"]


class ExecutionScope:
    """每次调用重新授权，来源热切换不能借用旧执行上下文。"""

    def __init__(
        self,
        *,
        principal: str,
        config: CodeModeConfig,
        config_loader: Callable[[], CodeModeConfig],
        principal_loader: Callable[[], str | None],
        directory: CodeModeProvider,
        sources: Callable[[], dict[str, SourceGeneration]],
    ) -> None:
        self.principal, self.config = principal, config
        self.config_loader, self.principal_loader = config_loader, principal_loader
        self.directory, self.sources = directory, sources
        self.source_snapshot = sources()
        self.started = monotonic()
        self.request_id = str(uuid4())
        self.closed = False
        self.stopped: str | None = None
        self.last_error: str | None = None
        self.attempted = 0
        self.calls: list[CallRecord] = []
        self.pending: set[asyncio.Task] = set()

    def stop(self, reason: str) -> None:
        """封闭后不能通过在客户端代码捕获异常来重新调度调用。"""
        self.stopped = self.stopped or reason
        raise ExecutionStopped(self.stopped)

    def check(self) -> None:
        """授权、有效配置和总 deadline 不依赖旧目录快照。"""
        if self.closed or self.stopped:
            raise ExecutionStopped(self.stopped or "execution_closed")
        if self.principal_loader() != self.principal:
            self.stop("authorization_changed")
        current = self.config_loader()
        fields = set(CodeModeConfig.model_fields)
        if current.model_dump(include=fields) != self.config.model_dump(include=fields):
            self.stop("configuration_changed")
        if monotonic() - self.started >= self.config.limits.deadline_seconds:
            self.stop("deadline")

    def permits_source(self, tool: Tool) -> bool:
        """供 SDK 最后一次 dispatch 校验使用，来源对象改变就拒绝。"""
        self.check()
        source = tool.get_meta().get(ORIGIN_META, {}).get("service")
        original = self.source_snapshot.get(source)
        return original is not None and original.matches(self.sources().get(source))

    async def _invoke(
        self,
        callback: Callable[..., Awaitable],
        record: CallRecord,
        name: str,
        params: dict,
    ) -> Any:
        try:
            self.check()
            tool = await self.directory.get_tool(name)
            self.check()
            if tool is None:
                self.stop("tool_denied")
            if not self.permits_source(tool):
                self.stop("source_changed")
            record.name = tool.name
            record.source = tool.get_meta()[ORIGIN_META]["service"]
            result = await callback(name, params)
            record.outcome = "success"
            self.check()
            if not self.permits_source(tool):
                self.stop("source_changed")
            return result
        except ExecutionStopped:
            if record.outcome == "pending":
                record.outcome = "denied"
            raise
        except asyncio.CancelledError:
            if record.outcome != "success":
                record.outcome = "cancelled"
            raise
        except Exception:
            record.outcome = "error"
            self.last_error = "tool_error"
            raise ToolError("CodeMode tool call failed; input/result omitted") from None
        finally:
            record.duration_ms = int((monotonic() - record.started) * 1000)

    async def call(
        self, callback: Callable[..., Awaitable], args: tuple, kwargs: dict
    ) -> Any:
        """尝试先计数；工具超时也覆盖目录查询与底层等待。"""
        self.attempted += 1
        self.check()
        if self.attempted > self.config.limits.max_tool_calls:
            self.stop("call_limit")
        if (
            len([task for task in self.pending if not task.done()])
            >= self.config.limits.tool_concurrency
        ):
            self.stop("tool_concurrency")
        record = CallRecord()
        self.calls.append(record)
        try:
            name, params = parse_tool_call(args, kwargs)
        except ExecutionStopped:
            record.outcome = "invalid"
            self.last_error = "invalid_call"
            raise ToolError("CodeMode invalid call arguments") from None
        task = asyncio.create_task(self._invoke(callback, record, name, params))
        self.pending.add(task)
        task.add_done_callback(self._consume)
        try:
            done, _ = await asyncio.wait(
                {task}, timeout=self.config.limits.tool_timeout_seconds
            )
            if not done:
                task.cancel()
                self.stop("tool_timeout")
            return task.result()
        except asyncio.CancelledError:
            task.cancel()
            raise
        finally:
            if not task.done():
                with anyio.CancelScope(shield=True):
                    await asyncio.wait({task}, timeout=CLEANUP_SECONDS)
                if not task.done():
                    record.outcome = "cancel_unconfirmed"

    def _consume(self, task: asyncio.Task) -> None:
        self.pending.discard(task)
        if not task.cancelled():
            task.exception()

    async def cleanup(self) -> set[asyncio.Task]:
        """有限清理；无法确认取消的任务保留配额，不能伪造已停止。"""
        self.closed = True
        pending = {task for task in self.pending if not task.done()}
        for task in pending:
            task.cancel()
        if pending:
            with anyio.CancelScope(shield=True):
                _, pending = await asyncio.wait(pending, timeout=CLEANUP_SECONDS)
        return pending

    def summary(self, outcome: str, reason: str | None) -> dict:
        """有界、无敏感载荷的最终快照。"""
        return {
            "principal": self.principal,
            "request_id": self.request_id,
            "outcome": outcome,
            "reason": reason,
            "attempted": self.attempted,
            "completed": sum(c.outcome == "success" for c in self.calls),
            "duration_ms": int((monotonic() - self.started) * 1000),
            "calls": [
                {
                    "name": c.name,
                    "source": c.source,
                    "outcome": c.outcome,
                    "duration_ms": c.duration_ms,
                }
                for c in self.calls
            ],
        }

    def diagnostic(self, reason: str) -> ToolError:
        """部分完成不可回滚；不发送原始代码或上游异常文字。"""
        completed = sum(c.outcome == "success" for c in self.calls)
        return ToolError(
            f"CodeMode stopped ({reason}); completed {completed}/{self.attempted} calls. "
            "Completed operations are not rolled back; cancellation cannot guarantee undoing upstream actions."
        )
