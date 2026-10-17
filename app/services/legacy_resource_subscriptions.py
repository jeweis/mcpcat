"""旧客户端资源订阅，后台连接由服务生命周期管理器拥有。"""

import asyncio
from collections.abc import Awaitable, Callable
from hashlib import sha256
from typing import Any

import anyio
from fastmcp import Client
from starlette.requests import Request
from mcp.shared.exceptions import MCPError
from mcp.shared.subscriptions import (
    ResourceUpdated,
    ResourcesListChanged,
    ToolsListChanged,
    PromptsListChanged,
)
from mcp.types import INVALID_REQUEST, EmptyResult, SubscriptionFilter

from app.services.upstream_subscriptions import LegacyEvents, upstream_events
from app.services.async_compat import cancel_task_once


class LegacyResourceSubscriptions:
    def __init__(
        self,
        factory: Callable[..., Client],
        authorized: Callable[[Request], bool],
        schedule: Callable[[Awaitable[None]], asyncio.Task],
        identity: Callable[[Request], str],
        max_streams: int = 32,
    ) -> None:
        self.factory = factory
        self.authorized = authorized
        self.schedule = schedule
        self.identity = identity
        self.max_streams = max_streams
        self.entries = {}
        self.events_factory = upstream_events

    async def handle(self, context: Any, request: Any) -> EmptyResult:
        if not self.authorized(request):
            raise MCPError(INVALID_REQUEST, "Subscription access denied")
        message = context.message
        uri = str(message["uri"] if isinstance(message, dict) else message.uri)
        if len(uri) > 4096:
            raise MCPError(INVALID_REQUEST, "Resource URI limit exceeded")
        # SDK 会话 ID 与平台身份共同隔离，不保存明文密钥到任务索引。
        identity = sha256(self.identity(request).encode()).hexdigest()
        key = (context.fastmcp_context.session_id, identity, uri)
        if context.method == "resources/unsubscribe":
            entry = self.entries.get(key)
            if entry is not None:
                cancel_task_once(entry[1])
                await asyncio.gather(entry[1], return_exceptions=True)
            return EmptyResult()
        entry = self.entries.get(key)
        created = entry is None
        if entry is None:
            entry = self._start(key, request, context.fastmcp_context.session, uri)
        try:
            await asyncio.shield(entry[0])
        except BaseException:
            if created:
                cancel_task_once(entry[1])
                await asyncio.gather(entry[1], return_exceptions=True)
            raise
        return EmptyResult()

    async def watch_lists(self, context: Any, request: Request) -> None:
        """有界等待监听就绪，来源不支持或超时不影响已成功的读取。"""
        if not self.authorized(request):
            return
        identity = sha256(self.identity(request).encode()).hexdigest()
        key = (context.fastmcp_context.session_id, identity, None)
        entry = self.entries.get(key)
        created = entry is None
        if created:
            if len(self.entries) >= self.max_streams:
                return
            entry = self._start(key, request, context.fastmcp_context.session, None)
        try:
            with anyio.move_on_after(5) as scope:
                await asyncio.shield(entry[0])
            if scope.cancel_called and created:
                cancel_task_once(entry[1])
                await asyncio.gather(entry[1], return_exceptions=True)
        except MCPError:
            return

    def _start(self, key, request, session, uri):
        if len(self.entries) >= self.max_streams:
            raise MCPError(INVALID_REQUEST, "Subscription limit reached")
        ready = asyncio.get_running_loop().create_future()
        # 列表监听没有等待者，仍消费异常，避免未取回 Future 异常。
        ready.add_done_callback(
            lambda result: None if result.cancelled() else result.exception()
        )
        task = self.schedule(self._run(request, session, uri, ready))
        entry = (ready, task)
        self.entries[key] = entry

        def finished(done):
            if self.entries.get(key) is entry:
                self.entries.pop(key, None)
            if not ready.done():
                ready.cancel()

        task.add_done_callback(finished)
        return entry

    async def _run(self, request, session, uri, ready) -> None:
        legacy = LegacyEvents()
        try:
            async with legacy.send, legacy.receive, self.factory(
                message_handler=legacy
            ) as client:
                requested = (
                    SubscriptionFilter(resource_subscriptions=[uri])
                    if uri is not None
                    else SubscriptionFilter(
                        tools_list_changed=True,
                        resources_list_changed=True,
                        prompts_list_changed=True,
                    )
                )
                async with self.events_factory(client, requested, legacy) as stream:
                    if not self.authorized(request):
                        raise MCPError(INVALID_REQUEST, "Subscription access revoked")
                    if uri is not None and uri not in (
                        stream.honored.resource_subscriptions or []
                    ):
                        raise MCPError(
                            INVALID_REQUEST,
                            "Upstream did not accept resource subscription",
                        )
                    if uri is None and not any(
                        (
                            stream.honored.tools_list_changed,
                            stream.honored.resources_list_changed,
                            stream.honored.prompts_list_changed,
                        )
                    ):
                        ready.set_result(False)
                        return
                    ready.set_result(True)

                    async def guard():
                        while self.authorized(request):
                            await anyio.sleep(1)
                            try:
                                with anyio.fail_after(2):
                                    await session.send_ping()
                            except Exception:
                                break
                        group.cancel_scope.cancel()

                    async with anyio.create_task_group() as group:
                        group.start_soon(guard)
                        try:
                            async for event in stream:
                                if not self.authorized(request):
                                    break
                                if (
                                    isinstance(event, ResourceUpdated)
                                    and event.uri == uri
                                ):
                                    await session.send_resource_updated(uri)
                                elif uri is None:
                                    if isinstance(event, ToolsListChanged):
                                        await session.send_tool_list_changed()
                                    elif isinstance(event, ResourcesListChanged):
                                        await session.send_resource_list_changed()
                                    elif isinstance(event, PromptsListChanged):
                                        await session.send_prompt_list_changed()
                        finally:
                            group.cancel_scope.cancel()
        except Exception:
            if not ready.done():
                ready.set_exception(
                    MCPError(INVALID_REQUEST, "Resource subscription failed")
                )
            # 后续流失败只清理，不重连、不重放；不记录来源异常中的秘密。
