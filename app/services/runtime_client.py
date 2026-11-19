"""仅在初始化失败时有限恢复，业务方法仍由 FastMCP 处理且不重放。"""

from typing import Any

import anyio
from fastmcp.server.providers.proxy import ProxyClient

from app.services.diagnostic_logs import diagnostic
from app.services.stdio_compatibility import (
    StartupPreparationError,
    explicit_dependencies,
)
from app.services.upstream_startup import STARTUP_TIMEOUT


def uv_tool_prefix(command: str, args: list[str]) -> list[str] | None:
    """识别 uv 工具参数边界，不执行任意 --help 或修改其他启动器。"""
    from pathlib import Path

    name = Path(command).name.lower()
    index = 2 if name in {"uv", "uv.exe"} and args[:2] == ["tool", "run"] else 0
    if index == 0 and name not in {"uvx", "uvx.exe"}:
        return None
    while index < len(args):
        item = args[index]
        if not item.startswith("-"):
            return args[:index]
        if item in {"--from", "--with", "--python", "--index-url", "--default-index"}:
            index += 2
        elif item in {"--offline", "--refresh", "--no-cache", "--native-tls"}:
            index += 1
        else:
            return None
    return None


class RuntimeProxyClient(ProxyClient):
    """使用 SDK 公开 close/context/mode 接口重新建立连接，最多恢复一次。"""

    def __init__(
        self,
        *args: Any,
        runtime_transport: Any = None,
        recovery: bool = True,
        protocol_auto: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.runtime_transport = runtime_transport
        self.recovery = recovery
        self.protocol_auto = protocol_auto

    async def __aenter__(self) -> "RuntimeProxyClient":
        """总启动预算覆盖首次连接与恢复，不增加无限重试窗口。"""
        with anyio.fail_after(STARTUP_TIMEOUT):
            return await self._open_with_recovery()

    async def _open_with_recovery(self) -> "RuntimeProxyClient":
        try:
            return await super().__aenter__()
        except Exception as error:
            transport = self.runtime_transport
            attempt = getattr(transport, "attempt", None)
            if not self.recovery or not attempt or attempt.ready:
                self._raise_failure(error, attempt)
            action = self._recovery_action(attempt)
            if not action:
                self._raise_failure(error, attempt)
            try:
                await self.close()
            except Exception:
                await self.transport.close()
            if action == "new_environment":
                transport.replace_failed_cache()
                if self.protocol_auto:
                    self.mode = "auto"
            elif action == "legacy":
                self.mode = "legacy"
            else:
                transport.recovery_args = action
                transport.recovery_profile = "python-mcp1-import"
            diagnostic(
                "初始化失败，使用新连接执行一次兼容恢复",
                level="WARNING",
                server=attempt.server,
                operation="recovery",
                detail=(
                    "新连接 legacy"
                    if action == "legacy"
                    else (
                        "已保留失败缓存并创建新修订"
                        if action == "new_environment"
                        else "独立 uv 工具环境约束 mcp<2"
                    )
                ),
                attempt_id=attempt.id,
                stage=attempt.stage,
                error_code=attempt.category,
            )
            try:
                return await super().__aenter__()
            except Exception as failure:
                self._raise_failure(failure, getattr(transport, "attempt", None))

    def _raise_failure(self, error: BaseException, attempt: Any) -> None:
        from app.services.startup_errors import startup_error_message

        if startup_error_message(error):
            raise error
        if attempt and not attempt.ready and attempt.category:
            from app.services.startup_errors import MESSAGES

            code = "RUNTIME_" + attempt.category.upper()
            if code in MESSAGES:
                raise StartupPreparationError(code) from error
        raise error

    def _recovery_action(self, attempt: Any) -> str | list[str] | None:
        environment = getattr(self.runtime_transport, "runtime_environment", None)
        if (
            attempt.category == "cache"
            and any(
                word in attempt.stderr.upper() for word in ("ENOTEMPTY", "EINTEGRITY")
            )
            and environment
            and environment.manager in {"npm", "uv"}
            and not environment.external
            and not environment.snapshot()["external_caches"]
        ):
            return "new_environment"

        if (
            attempt.closed_rpc
            and attempt.discover_closed
            and attempt.category == "connection_closed"
            and attempt.exit_code in {None, 0}
            and self.mode == "auto"
        ):
            return "legacy"
        transport = self.runtime_transport
        text = attempt.stderr
        if (
            attempt.category == "dependency"
            and "ModuleNotFoundError" in text
            and "mcp.server.fastmcp" in text
        ):
            prefix = uv_tool_prefix(transport.original_command, transport.original_args)
            if prefix is not None and not explicit_dependencies(
                prefix, transport.original_env
            ):
                return [
                    *prefix,
                    "--with",
                    "mcp<2",
                    *transport.original_args[len(prefix) :],
                ]
        return None
