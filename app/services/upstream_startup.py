"""仅序列化 npx 的启动握手，不复用进程、协议会话或用户回调。"""

import time
from pathlib import Path
from typing import Any

import anyio
from filelock import FileLock, Timeout

from app.core.config import settings

STARTUP_TIMEOUT = 120.0
INSPECTOR_REQUEST_TIMEOUT = STARTUP_TIMEOUT + 25


class StartupGate:
    """同一数据目录的 worker 使用 OS 锁；退出或取消时由所属连接释放。"""

    def __init__(self, command: str, timeout: float = STARTUP_TIMEOUT) -> None:
        self.deadline = time.monotonic() + timeout
        self.lock: FileLock | None = None
        self.negotiated = False
        if Path(command).name.lower() in {"npx", "npx.cmd", "npx.exe"}:
            path = Path(settings.mcpcat_database_path).with_suffix(".npx-startup.lock")
            path.parent.mkdir(parents=True, exist_ok=True)
            self.lock = FileLock(str(path), timeout=0, thread_local=False)

    def remaining(self) -> float:
        """排队和初始化共用一次预算，不因重试重新计时。"""
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("上游启动等待超时，请查看诊断日志")
        return remaining

    async def acquire(self) -> None:
        """非阻塞尝试 OS 锁，不将等待放进无法取消的工作线程。"""
        if self.lock is None:
            return
        while True:
            remaining = self.remaining()
            try:
                self.lock.acquire()
                return
            except Timeout:
                await anyio.sleep(min(0.05, remaining))

    def release(self) -> None:
        """释放本连接持有的锁；未持有锁或重复调用均无副作用。"""
        if self.lock is not None and self.lock.is_locked:
            self.lock.release()


def startup_session(base: type, gate: StartupGate, request_timeout: float) -> type:
    """通过公开 Session API 区分首次协商和普通请求的等待预算。"""

    class StartupSession(base):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["read_timeout_seconds"] = gate.remaining()
            super().__init__(*args, **kwargs)

        def adopt(self, result: Any) -> None:
            """现代 discover 和 legacy initialize 完成后即可并行运行。"""
            super().adopt(result)
            gate.negotiated = True
            gate.release()

        async def send_request(
            self,
            request: Any,
            result_type: Any,
            request_read_timeout_seconds: float | None = None,
            **kwargs: Any,
        ) -> Any:
            """初始化使用剩余启动预算，普通请求保持来源原来的超时。"""
            if request_read_timeout_seconds is None:
                request_read_timeout_seconds = (
                    gate.remaining()
                    if self.protocol_version is None
                    else request_timeout
                )
            return await super().send_request(
                request, result_type, request_read_timeout_seconds, **kwargs
            )

    return StartupSession
