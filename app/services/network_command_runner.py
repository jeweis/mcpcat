"""有界、可取消的命令进程；输出仅在当前认证响应中实时发送。"""

import asyncio
import json
import os
import signal
import time
from collections.abc import AsyncIterator
from typing import Any

import anyio

from app.services.diagnostic_logs import DiagnosticLogs
from app.services.diagnostic_redaction import redact
from app.services.network_commands import NetworkCommand, command_environment
from app.services.shell_commands import ShellCommand

OUTPUT_LIMIT = 65536
LINE_LIMIT = 4096


def event(kind: str, **values: Any) -> bytes:
    """编码一条 NDJSON 事件，浏览器按行消费而非缓冲整个响应。"""
    return (json.dumps({"type": kind, **values}, ensure_ascii=True) + "\n").encode()


async def stop_process(process: asyncio.subprocess.Process) -> None:
    """终止 POSIX 进程组或 Windows 子进程树，再排空管道回收。"""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.returncode is None:
            taskkill = os.path.join(
                os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32", "taskkill.exe"
            )
            killer = await asyncio.create_subprocess_exec(
                taskkill,
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
            if process.returncode is None:
                process.kill()
    except ProcessLookupError:
        pass
    # kill 后排空管道，避免输出上限触发时 wait 因满管道无法完成。
    await process.communicate()


class OutputLimitError(Exception):
    """原始输出已达上限，必须终止进程而非继续读取。"""


async def output_lines(reader: asyncio.StreamReader) -> AsyncIterator[str]:
    """输出按完整行脱敏，超长行整体省略，不把秘密拆成多个片段。"""
    pending = b""
    dropping = False
    total = 0
    while chunk := await reader.read(4096):
        total += len(chunk)
        if total > OUTPUT_LIMIT:
            raise OutputLimitError
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            if not dropping:
                yield (
                    line.decode("utf-8", "replace") + "\n"
                    if len(line) <= LINE_LIMIT
                    else "[输出单行超过 4096 字节，已省略]\n"
                )
            dropping = False
        if len(pending) > LINE_LIMIT:
            if not dropping:
                yield "[输出单行超过 4096 字节，已省略]\n"
            pending = b""
            dropping = True
    if pending and not dropping:
        yield pending.decode("utf-8", "replace")


class NetworkCommandRunner:
    """每个 worker 同时最多两项命令，不保存其他用户的输出或可恢复会话。"""

    def __init__(self, store: DiagnosticLogs) -> None:
        self.store = store
        self.running = 0

    async def events(
        self, command: NetworkCommand | ShellCommand, args: list[str]
    ) -> AsyncIterator[bytes]:
        """运行诊断，断开连接、取消或超时均回收进程。"""
        if self.running >= 2:
            yield event("result", status="busy", message="已有两项命令运行，请稍后重试")
            return
        self.running += 1
        started = time.monotonic()
        process = None
        status = "failed"
        try:
            self.store.record(
                "命令执行开始", level="INFO", operation=f"command/{command.kind}"
            )
            with anyio.CancelScope(shield=True):
                process = await self._spawn(args)
            yield event(
                "start",
                message="正在 MCPCat 所在服务器/容器执行",
                timeout=command.timeout,
            )
            async for item in self._consume(process, command.timeout):
                if item["type"] == "result":
                    status = item["status"]
                    item["duration_ms"] = round((time.monotonic() - started) * 1000)
                yield event(item.pop("type"), **item)
        except (asyncio.CancelledError, GeneratorExit):
            if status == "failed":
                status = "cancelled"
            raise
        except OSError as error:
            yield event("result", status="failed", message=self._safe(str(error)))
        finally:
            await self._cleanup(process, status, command.kind)

    async def _spawn(self, args: list[str]) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
            env=command_environment(),
            start_new_session=os.name == "posix",
        )

    async def _cleanup(
        self, process: asyncio.subprocess.Process | None, status: str, kind: str
    ) -> None:
        if process is not None:
            with anyio.CancelScope(shield=True):
                await stop_process(process)
        self.running -= 1
        self.store.record(
            f"命令执行结束：{status}",
            level="INFO" if status == "completed" else "WARNING",
            operation=f"command/{kind}",
        )

    async def _consume(
        self, process: asyncio.subprocess.Process, timeout: float
    ) -> AsyncIterator[dict]:
        deadline = time.monotonic() + timeout
        lines = output_lines(process.stdout)
        try:
            while True:
                try:
                    line = await asyncio.wait_for(
                        anext(lines), max(0, deadline - time.monotonic())
                    )
                except StopAsyncIteration:
                    break
                yield {"type": "output", "text": self._safe(line)}
            code = await asyncio.wait_for(
                process.wait(), max(0, deadline - time.monotonic())
            )
            yield {
                "type": "result",
                "status": "completed" if code == 0 else "failed",
                "exit_code": code,
                "truncated": False,
            }
        except OutputLimitError:
            yield {
                "type": "result",
                "status": "output_limit",
                "truncated": True,
                "message": "输出超过 64 KiB，已停止执行",
            }
        except TimeoutError:
            yield {
                "type": "result",
                "status": "timed_out",
                "message": "执行超时，已停止进程",
            }
        finally:
            await lines.aclose()

    def _safe(self, text: str) -> str:
        with self.store.lock:
            return redact(text, self.store.secrets)
