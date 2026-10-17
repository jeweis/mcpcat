"""Web 隔离夹具的响应交付计数，不记录认证头或用户正文。"""

import hashlib

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class DeliveryProbe:
    """只观察本夹具的完整下载响应，不替换下载内容或生产路由。"""

    def __init__(self, app: ASGIApp, counters: dict) -> None:
        self.app, self.counters = app, counters

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.endswith("/download"):
            await self.app(scope, receive, send)
            return
        digest, size, status = hashlib.sha256(), 0, 0

        async def observe(message: Message) -> None:
            nonlocal size, status
            if message["type"] == "http.response.start":
                status = message["status"]
            if message["type"] == "http.response.body":
                content = message.get("body", b"")
                digest.update(content)
                size += len(content)
            await send(message)
            if message["type"] == "http.response.body" and not message.get(
                "more_body", False
            ):
                self.counters["downloads"].append(
                    {
                        "path": path,
                        "status": status,
                        "size": size,
                        "sha256": digest.hexdigest(),
                    }
                )

        await self.app(scope, receive, observe)
