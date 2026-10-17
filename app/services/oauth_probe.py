"""不触发交互授权的 MCP 探测；协议时代协商交给公开 Client。"""

from dataclasses import dataclass
from typing import Any

import httpx2
from exceptiongroup import BaseExceptionGroup
from fastmcp import Client
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from mcp.shared.exceptions import MCPError
from mcp_types import UNSUPPORTED_PROTOCOL_VERSION

from app.services.async_compat import operation_timeout


class OAuthProbeError(RuntimeError):
    """只包含固定类别的安全诊断，不暴露 URL、响应正文或凭证。"""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(f"MCP 授权探测失败（{category}），请检查上游连接与协议配置")


@dataclass(frozen=True)
class ProbeResult:
    """成功协议版本或需要授权的原始 challenge，后者不写入日志。"""

    protocol_version: str | None = None
    challenge: httpx2.Response | None = None


def _causes(error: BaseException) -> list[BaseException]:
    seen, pending, result = set(), [error], []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        result.append(current)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        if current.__cause__:
            pending.append(current.__cause__)
        elif current.__context__:
            pending.append(current.__context__)
    return result


def _category(error: BaseException, statuses: list[int]) -> str:
    causes = _causes(error)
    for item in causes:
        if isinstance(item, OAuthProbeError):
            return item.category
    if any(
        isinstance(item, (TimeoutError, httpx2.TimeoutException)) for item in causes
    ):
        return "timeout"
    if any(isinstance(item, httpx2.TransportError) for item in causes):
        return "connection"
    if any(
        isinstance(item, MCPError) and item.code == UNSUPPORTED_PROTOCOL_VERSION
        for item in causes
    ):
        return "protocol_version"
    return "http" if any(status >= 400 for status in statuses) else "protocol"


async def probe_mcp_auth(
    url: str,
    headers: dict[str, str] | None = None,
    server_type: str = "streamable-http",
) -> ProbeResult:
    """只有实际 401 进入授权发现；超时/版本/连接失败不冒充无需认证。"""
    challenge = None
    statuses = []

    async def observe(response: httpx2.Response) -> None:
        nonlocal challenge
        statuses.append(response.status_code)
        if response.status_code == 401:
            challenge = response
            response.raise_for_status()
        if response.is_redirect:
            source = response.request.url
            target = source.join(response.headers["location"])
            if (
                (target.scheme, target.host, target.port)
                != (source.scheme, source.host, source.port)
                or target.username
                or target.password
            ):
                raise OAuthProbeError("redirect")

    def factory(**kwargs: Any) -> httpx2.AsyncClient:
        kwargs["follow_redirects"] = True
        return httpx2.AsyncClient(**kwargs, event_hooks={"response": [observe]})

    transport_type = SSETransport if server_type == "sse" else StreamableHttpTransport
    try:
        transport = transport_type(
            url, headers=dict(headers or {}), auth=None, httpx_client_factory=factory
        )
        async with operation_timeout(12), Client(
            transport, mode="auto", timeout=10, init_timeout=10
        ) as client:
            return ProbeResult(protocol_version=client.protocol_version)
    except Exception as error:
        if challenge is not None:
            return ProbeResult(challenge=challenge)
        raise OAuthProbeError(_category(error, statuses)) from None
