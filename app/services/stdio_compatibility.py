"""已知工具启动兼容：先检查原环境，仅对明确的 mcpdoc 导入故障修复。"""

import asyncio
import os
import re
from pathlib import Path

import anyio
from mcp.client.stdio import get_default_environment

from app.services.diagnostic_logs import diagnostic, diagnostic_store

MCPDOC_FIX = "mcpdoc-mcp1-import"


class StartupPreparationError(RuntimeError):
    """可安全穿过 MCP 代理的启动错误，不包含原始输出或配置秘密。"""

    def __init__(self, code: str) -> None:
        from app.services.startup_errors import MESSAGES

        super().__init__(f"{MESSAGES.get(code, code)} [MCPCAT_STARTUP:{code}]")


def mcpdoc_prefix(command: str, args: list[str]) -> list[str] | None:
    """只匹配已知 uv 工具调用；不重写 shell、路径包或其他 Python 服务。"""
    name = Path(command).name.lower()
    start = 0
    if name in {"uv", "uv.exe"} and args[:2] == ["tool", "run"]:
        start = 2
    elif name not in {"uvx", "uvx.exe"}:
        return None
    prefix = args[:start]
    index = start
    while index < len(args):
        item = args[index]
        if item == "mcpdoc":
            return prefix
        if item in {"--from", "--with", "--python", "--index-url", "--default-index"}:
            if index + 1 >= len(args):
                return None
            value = args[index + 1]
            if item == "--from" and not re.fullmatch(
                r"mcpdoc(?:[<>=!~][^\s;]+)?", value
            ):
                return None
            prefix.extend([item, value])
            index += 2
        elif item in {"--refresh", "--offline", "--no-cache", "--native-tls"}:
            prefix.append(item)
            index += 1
        else:
            return None
    return None


def explicit_dependencies(prefix: list[str], env: dict[str, str]) -> bool:
    """用户明确传入依赖或约束时，解释冲突而不覆盖其意图。"""
    return "--with" in prefix or any(
        key.startswith(("UV_CONSTRAINT", "UV_OVERRIDE", "UV_TOOL_CONSTRAINT"))
        for key in env
    )


class StdioCompatibility:
    """只缓存准备结果，不共享用户进程、协议会话、认证或回调。"""

    def __init__(
        self,
        command: str,
        args: list[str],
        env: dict[str, str],
        server: str,
        enabled: bool = True,
    ) -> None:
        self.command, self.args, self.env, self.server = (
            command,
            list(args),
            dict(env),
            server,
        )
        self.prefix = mcpdoc_prefix(command, args) if enabled else None
        self.lock = anyio.Lock()
        self.prepared: list[str] | None = None

    async def prepare(self) -> list[str]:
        """原环境通过则原样启动；确认旧导入故障后最多准备一次兼容环境。"""
        if self.prefix is None:
            return list(self.args)
        store = diagnostic_store()
        if store is not None:
            store.protect({"env": self.env})
        async with self.lock:
            if self.prepared is not None:
                if self.prepared != self.args:
                    self._report(
                        "使用已准备的 mcpdoc 兼容环境",
                        f"规则：{MCPDOC_FIX}；独立会话；原配置未修改",
                        "INFO",
                    )
                return list(self.prepared)
            code, text = await self._probe(self.prefix)
            if code == 0:
                self.prepared = list(self.args)
                return list(self.prepared)
            self._report("mcpdoc 原始环境检查失败", text, "WARNING")
            if not ("ModuleNotFoundError" in text and "mcp.server.fastmcp" in text):
                raise StartupPreparationError("PREPARE_FAILED")
            if explicit_dependencies(self.prefix, self.env):
                raise StartupPreparationError("DEPENDENCY_OVERRIDE")
            fixed = [*self.prefix, "--with", "mcp<2"]
            code, text = await self._probe(fixed)
            if code:
                self._report("mcpdoc 兼容环境准备失败", text, "ERROR")
                raise StartupPreparationError("MCPDOC_DEPENDENCY")
            position = len(self.prefix)
            self.prepared = [
                *self.args[:position],
                "--with",
                "mcp<2",
                *self.args[position:],
            ]
            self._report(
                "已自动应用 mcpdoc 启动兼容规则",
                f"规则：{MCPDOC_FIX}；独立工具环境约束 mcp<2；原配置未修改",
                "INFO",
            )
            return list(self.prepared)

    def _report(self, message: str, detail: str, level: str) -> None:
        diagnostic(
            message, server=self.server, operation="prepare", detail=detail, level=level
        )

    async def _probe(self, prefix: list[str]) -> tuple[int, str]:
        process = None
        try:
            with anyio.CancelScope(shield=True):
                process = await asyncio.create_subprocess_exec(
                    self.command,
                    *prefix,
                    "mcpdoc",
                    "--help",
                    env={**get_default_environment(), **self.env},
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=os.name == "posix",
                )
            tail = b""
            while chunk := await process.stderr.read(4096):
                tail = (tail + chunk)[-16000:]
            return await process.wait(), tail.decode("utf-8", "replace")
        except OSError:
            raise StartupPreparationError("EXECUTABLE_MISSING") from None
        finally:
            if process is not None:
                from app.services.network_command_runner import stop_process

                with anyio.CancelScope(shield=True):
                    await stop_process(process)
