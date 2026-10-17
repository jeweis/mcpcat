"""在 SDK 公开认证流边界约束机器授权，不实现 OAuth 发现或令牌交换。"""

from collections.abc import AsyncGenerator, Iterable
from typing import Any
from urllib.parse import parse_qs

import httpx2
from mcp.client.auth import OAuthFlowError


def machine_http_client(**kwargs: Any) -> httpx2.AsyncClient:
    """供公开 transport factory 使用；不跟随包含机器凭证的重定向。

    FastMCP 会传入 follow_redirects=True，必须覆盖而不是设置默认值。
    仅用于机器认证连接；管理员需填写最终 MCP 地址。
    """
    kwargs["follow_redirects"] = False
    return httpx2.AsyncClient(**kwargs)


class MachineOAuthGuard(httpx2.Auth):
    """拒绝超出管理员范围的令牌请求及授权结果，错误不包含秘密。"""

    requires_response_body = True

    def __init__(
        self, provider: httpx2.Auth, *, token_endpoint: str, scopes: Iterable[str]
    ) -> None:
        """包装已绑定 issuer 的 SDK Provider；令牌地址必须经过配置确认。"""
        self.provider = provider
        self.token_endpoint = httpx2.URL(token_endpoint)
        self.scopes = frozenset(scopes)

    def check_request(self, request: httpx2.Request) -> bool:
        """在 HTTP 发送前校验机器令牌请求，返回是否为令牌交换。"""
        if request.method != "POST":
            return False
        content_type = request.headers.get("content-type", "").split(";")[0]
        if content_type != "application/x-www-form-urlencoded":
            return False
        form = parse_qs(request.content.decode("utf-8"), keep_blank_values=True)
        if form.get("grant_type") != ["client_credentials"]:
            return False
        if request.url != self.token_endpoint:
            raise OAuthFlowError("Machine authorization token endpoint changed")
        requested = form.get("scope", [""])
        if len(requested) != 1 or not set(requested[0].split()) <= self.scopes:
            raise OAuthFlowError("Machine authorization scope exceeds configured range")
        return True

    def check_response(self, response: httpx2.Response) -> None:
        """拒绝授权服务器意外扩大授权范围；其他响应交由 SDK 校验。"""
        if response.is_redirect:
            raise OAuthFlowError(
                "Machine authorization token redirects are not allowed"
            )
        if response.status_code != 200:
            return
        try:
            body = response.json()
        except ValueError:
            return
        if not isinstance(body, dict) or "scope" not in body:
            return
        scope = body["scope"]
        if not isinstance(scope, str) or not set(scope.split()) <= self.scopes:
            raise OAuthFlowError(
                "Machine authorization granted scope exceeds configured range"
            )

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        """驱动公开 SDK auth flow；失败和取消均关闭原生成器。"""
        flow = self.provider.async_auth_flow(request)
        try:
            outgoing = await anext(flow)
            while True:
                await outgoing.aread()
                token_request = self.check_request(outgoing)
                response = yield outgoing
                if token_request:
                    await response.aread()
                    self.check_response(response)
                try:
                    outgoing = await flow.asend(response)
                except StopAsyncIteration:
                    return
        finally:
            await flow.aclose()
