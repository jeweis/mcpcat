"""会话私有的有界事件与独立订阅，协议生命周期交给 SDK。"""

import asyncio
import math
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import anyio
from fastmcp.client.messages import MessageHandler
from mcp.client.subscriptions import listen
from mcp.shared.subscriptions import ResourceUpdated
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

CHANGE_METHODS = {
    "notifications/tools/list_changed",
    "notifications/prompts/list_changed",
    "notifications/resources/list_changed",
    "notifications/resources/updated",
}


class InspectorEvents(MessageHandler):
    """只保留有限会话事件；不记录完整日志、凭证或任意通知 payload。"""

    def __init__(self) -> None:
        self.items: deque[dict] = deque(maxlen=200)
        self.sequence = 0
        self.worker: asyncio.Task | None = None
        self.subscription: dict = {"status": "stopped"}
        self.closed = False

    def append(self, kind: str, **data: Any) -> None:
        """追加有序事件，不持久化内容。"""
        if self.closed or self.subscription.get("status") == "revoked":
            return
        self.sequence += 1
        self.items.append({
            "id": self.sequence, "kind": kind,
            "time": datetime.now(timezone.utc).isoformat(), **data,
        })

    def snapshot(self, after: int = 0) -> dict:
        """游标读取，明确指出环形缓冲区丢弃的历史数量。"""
        first = self.items[0]["id"] if self.items else self.sequence + 1
        return {
            "events": [dict(row) for row in self.items if row["id"] > after],
            "cursor": self.sequence,
            "dropped": max(0, first - after - 1),
            "subscription": dict(self.subscription),
        }

    async def progress(
        self, progress: float, total: float | None, message: str | None,
    ) -> None:
        """收集真实协议进度；自由文本不进入诊断记录。"""
        if not math.isfinite(progress) or (total is not None and not math.isfinite(total)):
            return
        self.append("progress", progress=progress, total=total)

    async def on_notification(self, message: Any) -> None:
        """兼容旧通知；现代变化事件由 SDK listen 迭代器消费。"""
        method = message.method
        if (
            method not in CHANGE_METHODS
            or self.subscription.get("mode") != "legacy"
            or self.subscription.get("status") != "listening"
        ):
            return
        honored = self.subscription.get("honored", {})
        params = getattr(message, "params", None)
        uri = getattr(params, "uri", None)
        if method == "notifications/resources/updated":
            if uri is None or str(uri) not in honored.get("resourceSubscriptions", []):
                return
            self.append(method, uri=str(uri)[:4096])
        else:
            capability = method.split("/")[1]
            if honored.get(f"{capability}ListChanged") is True:
                self.append(method)

    async def log(self, message: Any) -> None:
        """替代默认日志回调，避免原始上游内容被写入服务端日志。"""
        self.append("log", level=message.level, content_omitted=True)

    def start(
        self, client: Any, filters: dict, authorized: Callable[[], bool],
    ) -> dict:
        """建立独立客户端，重复开始必须先显式停止，不自动重连。"""
        if self.closed or (self.worker is not None and not self.worker.done()):
            raise ValueError("订阅已在运行或会话已关闭，请先停止订阅")
        self.subscription = {"status": "connecting", "requested": filters}
        self.worker = asyncio.create_task(self._run(client.new(), filters, authorized))
        return dict(self.subscription)

    async def _guard(self, authorized: Callable[[], bool]) -> None:
        while True:
            if not authorized():
                self.subscription["status"] = "revoked"
                self.items.clear()
                raise PermissionError("会话身份已失效")
            await anyio.sleep(1)

    async def _run(self, client: Any, filters: dict, authorized: Callable[[], bool]) -> None:
        try:
            if not authorized():
                self.subscription["status"] = "revoked"
                self.items.clear()
                return
            async with client, anyio.create_task_group() as group:
                group.start_soon(self._guard, authorized)
                if client.protocol_version in MODERN_PROTOCOL_VERSIONS:
                    await self._modern(client, filters)
                else:
                    await self._legacy(client, filters)
                group.cancel_scope.cancel()
            self.subscription["status"] = "ended"
        except asyncio.CancelledError:
            self.subscription["status"] = "stopped"
        except Exception:
            if self.subscription.get("status") != "revoked":
                self.subscription["status"] = "failed"
                self.subscription["error"] = "订阅中断或未获支持；请检查端点后重新订阅并刷新内容"

    async def _modern(self, client: Any, filters: dict) -> None:
        self.subscription["mode"] = "modern"
        async with listen(client.session, **filters) as stream:
            self.subscription.update(
                status="listening",
                honored=stream.honored.model_dump(by_alias=True, exclude_none=True),
            )
            async for event in stream:
                if isinstance(event, ResourceUpdated):
                    self.append("notifications/resources/updated", uri=event.uri[:4096])
                else:
                    names = {
                        "ToolsListChanged": "tools", "PromptsListChanged": "prompts",
                        "ResourcesListChanged": "resources",
                    }
                    name = names.get(type(event).__name__)
                    if name:
                        self.append(f"notifications/{name}/list_changed")

    async def _legacy(self, client: Any, filters: dict) -> None:
        self.subscription["mode"] = "legacy"
        caps = client.server_capabilities.model_dump(by_alias=True, exclude_none=True)
        uris = filters.get("resource_subscriptions", [])
        if uris and not caps.get("resources", {}).get("subscribe"):
            raise ValueError("端点未声明资源订阅")
        subscribed = []
        try:
            for uri in uris:
                await client.session.subscribe_resource(uri)
                subscribed.append(uri)
            honored = {"resourceSubscriptions": subscribed}
            for key, capability in (
                ("tools", "tools"), ("prompts", "prompts"), ("resources", "resources"),
            ):
                honored[f"{key}ListChanged"] = bool(
                    filters.get(f"{key}_list_changed") and caps.get(capability, {}).get("listChanged")
                )
            self.subscription.update(status="listening", honored=honored)
            while True:
                await anyio.sleep(5)
                # 旧通知流没有现代 listen 迭代器的结束信号。
                # 使用公开 SDK ping 检测失联，不重连或重放订阅。
                with anyio.fail_after(5):
                    await client.ping()
        finally:
            with anyio.move_on_after(5, shield=True):
                for uri in subscribed:
                    await client.session.unsubscribe_resource(uri)

    async def stop(self) -> dict:
        """结束监听并等待 SDK 清理，不重放原工具调用。"""
        self.subscription["status"] = "stopping"
        if self.worker is not None and not self.worker.done():
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        self.subscription["status"] = "stopped"
        return dict(self.subscription)

    async def close(self) -> None:
        """会话关闭后清理内存并拒绝晚到事件。"""
        self.closed = True
        await self.stop()
        self.items.clear()
