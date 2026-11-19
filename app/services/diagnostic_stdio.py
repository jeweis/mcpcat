"""通过 SDK 的公开 log_file 接口采集 stderr，不将未脱敏文本落盘。"""

import codecs
import logging
import os
import re
import sys
import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, AsyncIterator

import anyio
from fastmcp.client.transports import StdioTransport
from fastmcp.client.transports.base import TransportOptions
from mcp import ClientSession

from app.services.diagnostic_logs import DiagnosticLogs, diagnostic, diagnostic_store
from app.services.diagnostic_redaction import redact
from app.services.runtime_attempts import RuntimeAttempt
from app.services.runtime_environments import RuntimeEnvironment
from app.services.stdio_compatibility import StdioCompatibility
from app.services.upstream_startup import STARTUP_TIMEOUT, StartupGate, startup_session


class DiagnosticStdioTransport(StdioTransport):
    """每个上游连接拥有独立管道，持续排空且限制单条 stderr 大小。"""

    def __init__(
        self,
        server_name: str = "",
        *,
        compatibility: StdioCompatibility | None = None,
        runtime_environment: RuntimeEnvironment | None = None,
        request_timeout: float = 30,
        startup_timeout: float = STARTUP_TIMEOUT,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.server_name = server_name
        self.compatibility = compatibility
        self.runtime_environment = runtime_environment
        self.original_command, self.original_args, self.original_env = (
            self.command,
            list(self.args),
            dict(self.env or {}),
        )
        self.recovery_args = (
            runtime_environment.recovery_args(self.original_args)
            if runtime_environment
            else None
        )
        self.recovery_profile = "python-mcp1-import" if self.recovery_args else None
        self.attempt = None
        self.request_timeout = request_timeout
        self.startup_timeout = startup_timeout

    @asynccontextmanager
    async def connect_session(
        self, *, transport_options: TransportOptions | None = None, **kwargs: Any
    ) -> AsyncIterator[ClientSession]:
        """SDK 负责进程退出；管道仅用于诊断，不触碰 stdout 协议流。"""
        gate = StartupGate(self.original_command, self.startup_timeout)
        attempt, temporary = self._new_attempt()
        gate.attempt = attempt
        options = transport_options or TransportOptions()
        options = replace(
            options,
            session_class=startup_session(
                options.session_class,
                gate,
                self.request_timeout,
            ),
        )
        try:
            await gate.acquire()
            await self._configure_process(gate)
            async with self._diagnosed_session(options, **kwargs) as session:
                yield session
        except TimeoutError as error:
            if not gate.negotiated:
                diagnostic(
                    "上游启动等待超时",
                    server=self.server_name,
                    operation="startup",
                    error=error,
                )
            raise
        except anyio.get_cancelled_exc_class():
            attempt.category = "cancelled"
            attempt.emit("上游连接已取消", "WARNING")
            raise
        except Exception as error:
            if not attempt.category:
                attempt.failed(error)
            raise
        finally:
            gate.release()
            attempt.finish()
            if temporary:
                temporary.cleanup()

    def replace_failed_cache(self) -> None:
        """保留旧缓存，新连接使用新环境；原始配置不修改。"""
        previous = self.runtime_environment
        previous.replace_failed_cache()
        self.runtime_environment = RuntimeEnvironment(
            self.server_name or "default", previous.config
        )
        self.original_env = self.runtime_environment.environment()
        self.env = dict(self.original_env)
        self.compatibility = StdioCompatibility(
            self.original_command,
            self.original_args,
            self.original_env,
            self.server_name,
        )
        self.recovery_args = None
        self.recovery_profile = "new-cache-revision"

    def _new_attempt(self):
        """每个连接独占监督元数据文件及日志上下文。"""
        self.attempt = attempt = RuntimeAttempt(
            self.server_name, self.runtime_environment
        )
        attempt.recovery_profile = self.recovery_profile
        temporary = (
            TemporaryDirectory(prefix="mcpcat-attempt-")
            if attempt.metadata is None
            else None
        )
        if temporary:
            attempt.metadata = Path(temporary.name) / "process.json"
        return attempt, temporary

    async def _configure_process(self, gate: StartupGate) -> None:
        """在启动预算内准备当前修订，随后交给 SDK 管理监督器生命周期。"""
        self.args = list(self.recovery_args or self.original_args)
        self.attempt.phase("prepare")
        if self.compatibility is not None and self.recovery_args is None:
            with anyio.fail_after(gate.remaining()):
                self.args = await self.compatibility.prepare()
        if self.runtime_environment:
            self.args = self.runtime_environment.cached_args(self.args)
        self.attempt.phase("spawn")
        self.command = sys.executable
        self.args = [
            str(Path(__file__).with_name("process_supervisor.py")),
            str(self.attempt.metadata),
            self.original_command,
            *self.args,
        ]

    @asynccontextmanager
    async def _diagnosed_session(
        self,
        options: TransportOptions,
        **kwargs: Any,
    ) -> AsyncIterator[ClientSession]:
        store = diagnostic_store() or DiagnosticLogs()
        store.protect({"env": self.env})
        read_fd, write_fd = os.pipe()
        output = os.fdopen(write_fd, "w", encoding="utf-8")
        reader = threading.Thread(
            target=_drain,
            args=(read_fd, store, self.server_name, self.attempt),
            daemon=True,
        )
        self.log_file = output
        reader.start()
        failure = None
        try:
            async with super().connect_session(
                transport_options=options, **kwargs
            ) as session:
                yield session
        except Exception as error:
            failure = error
            store.record(
                "上游 stdio 连接或请求失败",
                server=self.server_name,
                operation="upstream",
                error=error,
            )
        finally:
            output.close()
            self.log_file = None
            # 先让 SDK 关闭子进程，再等待管道尾部进入日志；不阻塞事件循环。
            with anyio.move_on_after(1, shield=True):
                await anyio.to_thread.run_sync(reader.join, abandon_on_cancel=True)
        if failure is not None:
            if self.attempt:
                self.attempt.failed(failure)
            raise failure


def _drain(
    read_fd: int,
    store: DiagnosticLogs,
    server_name: str,
    attempt: RuntimeAttempt | None = None,
) -> None:
    """分块读取，不因无换行或大量 stderr 占满内存/子进程管道。"""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    pending = ""
    dropping = False
    try:
        while chunk := os.read(read_fd, 4096):
            pending += decoder.decode(chunk)
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                if not dropping:
                    if attempt:
                        attempt.text(line)
                    _record(store, server_name, line, attempt)
                dropping = False
            if len(pending) > 4096:
                if not dropping:
                    _record(
                        store,
                        server_name,
                        "[stderr 单行超过 4096 字符，已省略]",
                        attempt,
                    )
                pending = ""
                dropping = True
        pending += decoder.decode(b"", final=True)
        if pending and not dropping:
            if attempt:
                attempt.text(pending)
            _record(store, server_name, pending, attempt)
    finally:
        os.close(read_fd)


def _record(
    store: DiagnosticLogs,
    server_name: str,
    text: str,
    attempt: RuntimeAttempt | None = None,
) -> None:
    if text.strip():
        if len(text) > 4096:
            text = "[stderr 单行超过 4096 字符，已省略]"
        failed = bool(
            re.match(r"(?i)\s*(npm (error|err!)|error\b|fatal\b|traceback\b)", text)
        )
        store.record(
            "上游进程标准错误输出",
            level="ERROR" if failed else "WARNING",
            server=server_name,
            operation="stderr",
            source="stdio",
            detail=text.rstrip(),
            attempt_id=attempt.id if attempt else None,
            stage=attempt.stage if attempt else None,
            environment_revision=(
                attempt.environment.revision
                if attempt and attempt.environment
                else None
            ),
        )
        # 保留容器终端的排障输出，但只输出脱敏后的文本；此 logger 不会重复入库。
        with store.lock:
            safe = redact(text.rstrip(), store.secrets)
            name = redact(server_name, store.secrets)
        logging.getLogger("mcpcat.stderr").log(
            logging.ERROR if failed else logging.WARNING, "[%s] %s", name, safe
        )
