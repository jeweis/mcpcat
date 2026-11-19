"""在 SDK 折叠 HTTP 错误前记录状态和网络原因，不读取认证头或正文。"""

from typing import Any

import httpx2


class RuntimeHttpClient(httpx2.AsyncClient):
    """通过公开 send 接口记录传输异常，维持原 HTTP 请求行为。"""

    def __init__(self, *, attempt=None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.attempt = attempt

    async def send(self, *args: Any, **kwargs: Any) -> httpx2.Response:
        """只记录异常类型和消息，交给所属尝试的脱敏日志处理。"""
        try:
            return await super().send(*args, **kwargs)
        except Exception as error:
            if self.attempt:
                self.attempt.text(type(error).__name__ + ": " + str(error))
            raise


def observe_http(transport: Any, owner: Any) -> None:
    """安装公开 client factory，保留用户工厂和 TLS 验证配置。"""
    original = transport.httpx_client_factory

    def factory(**kwargs: Any):
        attempt = owner.attempt
        if original:
            client = original(**kwargs)
        else:
            verify = getattr(transport, "verify", None)
            if verify is not None:
                kwargs["verify"] = verify
            client = RuntimeHttpClient(attempt=attempt, **kwargs)

        async def response(result):
            if attempt and result.status_code >= 400:
                attempt.text(f"HTTP status {result.status_code}")

        client.event_hooks.setdefault("response", []).append(response)
        return client

    transport.httpx_client_factory = factory
