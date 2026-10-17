"""旧协议 Registry 通知：每会话一个提交监听，SDK 负责发送和 ping。"""

import asyncio
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

import anyio
from mcp.shared.exceptions import MCPError
from mcp.shared.subscriptions import (
    PromptsListChanged,
    ResourcesListChanged,
    ResourceUpdated,
    ToolsListChanged,
)
from mcp.types import EmptyResult, INVALID_REQUEST

from app.services.async_compat import cancel_task_once


@dataclass
class LegacyRegistryListener:
    ready: asyncio.Future
    task: asyncio.Task | None = None
    uris: set[str] = field(default_factory=set)
    lists: bool = False
    changes: Any = None

    async def refresh(self) -> None:
        """资源集合改变时通知支持动态范围的事件源。"""
        refresh = getattr(self.changes, "refresh", None)
        if refresh is not None:
            await refresh()


class LegacyRegistrySubscriptions:
    """身份与 SDK 会话共同隔离；由 Registry 端点生命周期关闭。"""

    def __init__(self, owner: Any, principal: Any, schedule: Any = None) -> None:
        self.owner, self.principal = owner, principal
        self.schedule = schedule or asyncio.create_task
        self.entries: dict[tuple[str, str], LegacyRegistryListener] = {}

    async def handle(self, context: Any) -> EmptyResult:
        """只处理旧资源订阅及正常列表读取后的监听注册。"""
        if not self.owner.authorized():
            raise MCPError(INVALID_REQUEST, "Subscription access denied")
        key = (
            context.fastmcp_context.session_id,
            sha256(self.principal().encode()).hexdigest(),
        )
        is_resource = context.method in {"resources/subscribe", "resources/unsubscribe"}
        uri = None
        if is_resource:
            message = context.message
            uri = str(message["uri"] if isinstance(message, dict) else message.uri)
            if len(uri) > 4096:
                raise MCPError(INVALID_REQUEST, "Resource URI limit exceeded")
        entry = self.entries.get(key)
        if context.method == "resources/unsubscribe":
            if entry is not None:
                entry.uris.discard(uri)
                await entry.refresh()
                if not entry.lists and not entry.uris:
                    await self._stop(entry)
            return EmptyResult()
        if entry is None:
            if len(self.entries) >= 32:
                raise MCPError(INVALID_REQUEST, "Subscription limit reached")
            entry = LegacyRegistryListener(asyncio.get_running_loop().create_future())
            self.entries[key] = entry
            entry.task = self.schedule(
                self._run(key, entry, context.fastmcp_context.session)
            )
        if uri is not None:
            if uri not in entry.uris and len(entry.uris) >= 32:
                raise MCPError(INVALID_REQUEST, "Resource subscription limit exceeded")
            entry.uris.add(uri)
        else:
            entry.lists = True
        await entry.refresh()
        await asyncio.shield(entry.ready)
        accepts = getattr(entry.changes, "accepts", None)
        if uri is not None and accepts is not None and not accepts(uri):
            entry.uris.discard(uri)
            await entry.refresh()
            raise MCPError(INVALID_REQUEST, "Resource subscription was not accepted")
        return EmptyResult()

    async def _stop(self, entry: LegacyRegistryListener) -> None:
        if entry.task is not None:
            cancel_task_once(entry.task)
            await asyncio.gather(entry.task, return_exceptions=True)

    async def close(self) -> None:
        """停服时主动收尾，不等待下一次客户端 ping。"""
        await asyncio.gather(
            *(self._stop(entry) for entry in list(self.entries.values()))
        )

    async def _run(
        self, key: tuple, entry: LegacyRegistryListener, session: Any
    ) -> None:
        try:
            async with self.owner.watch_changes(entry.uris) as changes:
                entry.changes = changes
                previous = await self.owner.snapshot()
                entry.ready.set_result(True)

                async def guard() -> None:
                    while self.owner.authorized():
                        await anyio.sleep(0.5)
                        try:
                            with anyio.fail_after(2):
                                await session.send_ping()
                        except Exception:
                            break
                    group.cancel_scope.cancel()

                async with anyio.create_task_group() as group:
                    group.start_soon(guard)
                    try:
                        while True:
                            change = await changes.get()
                            if not self.owner.authorized():
                                break
                            current = await self.owner.snapshot()
                            events = await self.owner.events_for_change(
                                previous, current, entry.uris, change
                            )
                            previous = current
                            for event in events:
                                if not self.owner.authorized():
                                    return
                                await self._send(session, entry, event)
                    finally:
                        group.cancel_scope.cancel()
        except Exception:
            if not entry.ready.done():
                entry.ready.set_exception(
                    MCPError(INVALID_REQUEST, "Subscription unavailable")
                )
                entry.ready.exception()
        finally:
            if not entry.ready.done():
                entry.ready.cancel()
            if self.entries.get(key) is entry:
                self.entries.pop(key, None)

    async def _send(
        self, session: Any, entry: LegacyRegistryListener, event: Any
    ) -> None:
        if isinstance(event, ResourceUpdated) and event.uri in entry.uris:
            await session.send_resource_updated(event.uri)
        elif entry.lists:
            if (
                isinstance(event, ResourcesListChanged)
                and "resources" in self.owner.supported
            ):
                await session.send_resource_list_changed()
            elif (
                isinstance(event, ToolsListChanged) and "tools" in self.owner.supported
            ):
                await session.send_tool_list_changed()
            elif (
                isinstance(event, PromptsListChanged)
                and "prompts" in self.owner.supported
            ):
                await session.send_prompt_list_changed()
