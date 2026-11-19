"""HTTP/SSE 使用同一连接阶段诊断，不管理远程服务的本地依赖。"""

from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, AsyncIterator

import anyio
from fastmcp.client.transports.base import ClientTransport, TransportOptions
from mcp import ClientSession

from app.services.runtime_attempts import RuntimeAttempt
from app.services.runtime_http import observe_http
from app.services.upstream_startup import STARTUP_TIMEOUT, StartupGate, startup_session


class ObservedTransport(ClientTransport):
    """仅装饰公开 transport 选项，原认证与传输行为保持原样。"""

    def __init__(self, transport: ClientTransport, server: str, timeout: float) -> None:
        self.transport, self.server, self.timeout = transport, server, timeout
        self.legacy_only = transport.legacy_only
        self.attempt = None
        observe_http(transport, self)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.transport, name)

    @asynccontextmanager
    async def connect_session(
        self, *, transport_options: TransportOptions | None = None, **kwargs: Any
    ) -> AsyncIterator[ClientSession]:
        self.attempt = attempt = RuntimeAttempt(self.server)
        gate = StartupGate("http", STARTUP_TIMEOUT)
        gate.attempt = attempt
        options = transport_options or TransportOptions()
        options = replace(
            options,
            session_class=startup_session(options.session_class, gate, self.timeout),
        )
        try:
            async with self.transport.connect_session(
                transport_options=options, **kwargs
            ) as session:
                yield session
        except anyio.get_cancelled_exc_class():
            attempt.category = "cancelled"
            attempt.emit("上游连接已取消", "WARNING")
            raise
        except Exception as error:
            attempt.failed(error)
            raise
        finally:
            attempt.finish()

    async def close(self) -> None:
        """转发传输清理。"""
        await self.transport.close()

    def get_session_id(self) -> str | None:
        """只返回底层传输已有的会话标识。"""
        return self.transport.get_session_id()
