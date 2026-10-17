"""公开 SandboxProvider 的受限适配，生产只使用官方 Monty。"""

import asyncio
import re
from collections.abc import Callable
from typing import Any, NoReturn

from fastmcp.exceptions import ToolError
from fastmcp.experimental.transforms.code_mode import MontySandboxProvider

from app.models.code_mode_config import CodeModeConfig
from app.services.code_mode_cancellation import cancel_scopes
from app.services.code_mode_execution import (
    EXECUTION_SCOPE,
    ExecutionScope,
    ExecutionStopped,
    SourceGeneration,
    audit_execution,
)
from app.services.code_mode_leases import ExecutionLeasePool
from app.services.code_mode_provider import CodeModeProvider


class CodeModeSandboxProvider:
    """限制所有调度入口，Monty 不获得文件、shell、网络或凭证函数。"""

    def __init__(
        self,
        *,
        directory: CodeModeProvider,
        config_loader: Callable[[], CodeModeConfig],
        principal: Callable[[], str | None],
        sources: Callable[[], dict[str, SourceGeneration]],
        leases: ExecutionLeasePool,
        audit: Callable[[dict], None] = audit_execution,
    ) -> None:
        self.directory, self.config_loader = directory, config_loader
        self.principal, self.sources, self.leases, self.audit = (
            principal,
            sources,
            leases,
            audit,
        )
        self.active: dict[asyncio.Task, ExecutionScope] = {}
        self.retained: set[asyncio.Task] = set()

    async def run(
        self,
        code: str,
        *,
        inputs: dict | None = None,
        external_functions: dict[str, Callable] | None = None,
    ) -> Any:
        """只接收官方 call_tool；当前请求身份决定跨 worker 执行配额。"""
        config, principal = self._prepare(code, inputs, external_functions)
        scope, lease = self._acquire(config, principal)
        try:
            return await self._run_scope(code, scope, external_functions["call_tool"])
        finally:
            await self._finish(scope, lease)

    def _prepare(
        self, code: str, inputs: dict | None, external_functions: dict | None
    ) -> tuple[CodeModeConfig, str]:
        try:
            config = CodeModeConfig.model_validate(self.config_loader().model_dump())
            principal = self.principal()
        except Exception:
            self._reject(None, "configuration_or_identity_unavailable")
        if principal is None:
            self._reject(principal, "unavailable_or_unauthorized")
        if (
            inputs
            or set(external_functions or {}) != {"call_tool"}
            or not callable(external_functions["call_tool"])
        ):
            self._reject(principal, "uncontrolled_callback")
        if (
            not isinstance(code, str)
            or len(code.encode("utf-8")) > config.limits.max_code_bytes
        ):
            self._reject(principal, "code_size")
        return config, principal

    def _acquire(
        self, config: CodeModeConfig, principal: str
    ) -> tuple[ExecutionScope, Any]:
        try:
            lease = self.leases.acquire(principal, config.limits.principal_concurrency)
        except ValueError:
            self._reject(None, "invalid_identity")
        except OSError:
            self._reject(principal, "execution_slot_unavailable")
        if lease is None:
            self._reject(principal, "principal_concurrency")
        try:
            scope = ExecutionScope(
                principal=principal,
                config=config,
                config_loader=self.config_loader,
                principal_loader=self.principal,
                directory=self.directory,
                sources=self.sources,
            )
        except Exception:
            lease.release()
            self._reject(principal, "source_snapshot_unavailable")
        return scope, lease

    async def _finish(self, scope: ExecutionScope, lease: Any) -> None:
        pending = await scope.cleanup()
        if pending:
            observer = asyncio.create_task(self._release_after(pending, lease, scope))
            self.retained.add(observer)
            observer.add_done_callback(self.retained.discard)
        else:
            lease.release()

    async def _run_scope(
        self, code: str, scope: ExecutionScope, callback: Callable
    ) -> Any:
        task = asyncio.current_task()
        self.active[task] = scope
        token = EXECUTION_SCOPE.set(scope)
        outcome, reason = "success", None

        async def bounded(*args: Any, **kwargs: Any) -> Any:
            return await scope.call(callback, args, kwargs)

        try:
            scope.check()
            sandbox = MontySandboxProvider(limits=scope.config.limits.monty_limits())
            result = await asyncio.wait_for(
                sandbox.run(code, external_functions={"call_tool": bounded}),
                timeout=scope.config.limits.deadline_seconds,
            )
            scope.check()
            if any(
                call.outcome in {"pending", "cancelled", "cancel_unconfirmed"}
                for call in scope.calls
            ):
                scope.stop("unfinished_calls")
            return result
        except asyncio.CancelledError:
            outcome, reason = "cancelled", "cancelled"
            raise
        except Exception as error:
            outcome = "error"
            reason = (
                error.reason
                if isinstance(error, ExecutionStopped)
                else (
                    "deadline"
                    if isinstance(error, (TimeoutError, asyncio.TimeoutError))
                    else scope.stopped or scope.last_error or "sandbox_error"
                )
            )
            raise scope.diagnostic(reason) from None
        finally:
            self.active.pop(task, None)
            EXECUTION_SCOPE.reset(token)
            self.audit(scope.summary(outcome, reason))

    def _reject(self, principal: str | None, reason: str) -> NoReturn:
        self.audit(
            {
                "principal": (
                    principal
                    if principal and re.fullmatch(r"[a-f0-9]{64}", principal)
                    else "[unauthorized]"
                ),
                "outcome": "rejected",
                "reason": reason,
                "attempted": 0,
                "completed": 0,
            }
        )
        raise ToolError(f"CodeMode rejected ({reason})") from None

    async def _release_after(
        self, pending: set[asyncio.Task], lease: Any, scope: ExecutionScope
    ) -> None:
        try:
            await asyncio.gather(*pending, return_exceptions=True)
            self.audit(scope.summary("cleanup_finished", None))
        finally:
            lease.release()

    async def cancel_all(self) -> None:
        """管理关闭/热切换发起取消，不自动重试编排。"""
        await cancel_scopes(self.active, tuple(self.active), "configuration_changed")

    async def cancel_source(self, name: str) -> None:
        """取消已使用来源的编排；未决目录查询按快照保守处理。"""
        tasks = tuple(
            task
            for task, scope in self.active.items()
            if any(call.source == name for call in scope.calls)
            or (
                name in scope.source_snapshot
                and any(
                    not call.source and call.outcome == "pending"
                    for call in scope.calls
                )
            )
        )
        await cancel_scopes(self.active, tasks, "source_changed")
