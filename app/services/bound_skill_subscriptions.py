"""合并来源协议事件与附带 Skills 的事务事件，不将平台 URI 发给来源。"""

from contextlib import AsyncExitStack, asynccontextmanager
from collections.abc import AsyncIterator, Callable
from typing import Any

import anyio
from mcp.shared.exceptions import MCPError
from mcp.shared.subscriptions import ResourceUpdated, ToolsListChanged
from mcp.types import METHOD_NOT_FOUND, SubscriptionFilter

from app.services.registry_subscriptions import RegistrySubscriptions, VisibleSnapshot
from app.services.upstream_subscriptions import upstream_events
from app.services.subscription_context import subscription_snapshot_read

SKILL_PREFIX = "skill://mcpcat/"


class BoundSkillEvents:
    """每流一个有界队列，不与其他身份共享可见快照。"""

    def __init__(self, attachment: Any, honored: SubscriptionFilter) -> None:
        self.attachment, self.honored = attachment, honored
        self.send, self.receive = anyio.create_memory_object_stream(200)
        self.previous = VisibleSnapshot()
        self.lock = anyio.Lock()

    def __aiter__(self) -> AsyncIterator[Any]:
        return self.receive.__aiter__()

    async def snapshot(self) -> VisibleSnapshot:
        """复用当前身份的 Registry 与工具资格验证。"""
        with subscription_snapshot_read():
            registry = await self.attachment.registry()
        return VisibleSnapshot(
            resources={
                file["uri"]: file["digest"]
                for entry in registry.list_skills()
                for file in entry["resources"]
            }
        )

    def emit(self, event: Any) -> None:
        """满队列结束流，不阻塞来源通知接收。"""
        try:
            self.send.send_nowait(event)
        except (anyio.WouldBlock, anyio.ClosedResourceError):
            self.send.close()

    async def refresh(self) -> None:
        """串行比较快照，避免事务与来源事件同时触发重复变更。"""
        async with self.lock:
            current = await self.snapshot()
            for event in RegistrySubscriptions.changed_events(
                self.previous, current, self.honored.resource_subscriptions or []
            ):
                self.emit(event)
            self.previous = current

    async def watch(self, changes: Any) -> None:
        """只有实际提交信号触发平台快照刷新。"""
        while True:
            await changes.get()
            await self.refresh()

    async def forward(self, upstream: Any) -> None:
        """过滤来源伪造的平台命名空间，并处理工具依赖失效。"""
        try:
            async for event in upstream:
                if isinstance(event, ResourceUpdated) and event.uri.startswith(
                    SKILL_PREFIX
                ):
                    continue
                self.emit(event)
                if isinstance(event, ToolsListChanged):
                    # 工具目录改变可能使已绑定 Skill 不再满足 required_tools。
                    await self.refresh()
        finally:
            self.send.close()


def bound_skill_events(attachment: Any) -> Callable:
    """返回与公开 upstream_events 相同的上下文工厂，供新旧下游共用。"""

    @asynccontextmanager
    async def events(
        client: Any, requested: SubscriptionFilter, legacy: Any
    ) -> AsyncIterator[BoundSkillEvents]:
        local_uris = list(
            dict.fromkeys(
                uri
                for uri in requested.resource_subscriptions or []
                if uri.startswith(SKILL_PREFIX)
            )
        )
        remote_uris = [
            uri
            for uri in requested.resource_subscriptions or []
            if not uri.startswith(SKILL_PREFIX)
        ]
        forwarded = requested.model_copy(
            update={
                "resource_subscriptions": remote_uris or None,
                "tools_list_changed": bool(
                    requested.tools_list_changed
                    or requested.resources_list_changed
                    or local_uris
                ),
            }
        )
        async with AsyncExitStack() as stack:
            changes = await stack.enter_async_context(
                attachment.runtime.database.changes.watch()
            )
            merged = BoundSkillEvents(attachment, SubscriptionFilter())
            await stack.enter_async_context(merged.send)
            await stack.enter_async_context(merged.receive)
            merged.previous = await merged.snapshot()
            try:
                upstream = await stack.enter_async_context(
                    upstream_events(client, forwarded, legacy)
                )
                actual = upstream.honored
            except MCPError as error:
                # 只处理明确未实现通知的来源，不把认证、网络或业务错误伪装成成功。
                if error.code != METHOD_NOT_FOUND:
                    raise
                upstream, actual = None, SubscriptionFilter()
            honored = SubscriptionFilter(
                tools_list_changed=bool(
                    requested.tools_list_changed and actual.tools_list_changed
                ),
                prompts_list_changed=bool(
                    requested.prompts_list_changed and actual.prompts_list_changed
                ),
                resources_list_changed=bool(requested.resources_list_changed),
                resource_subscriptions=list(
                    dict.fromkeys([*(actual.resource_subscriptions or []), *local_uris])
                )
                or None,
            )
            merged.honored = honored
            async with merged.send, merged.receive:
                async with anyio.create_task_group() as group:
                    group.start_soon(merged.watch, changes)
                    if upstream is not None:
                        group.start_soon(merged.forward, upstream)
                    try:
                        yield merged
                    finally:
                        group.cancel_scope.cancel()

    return events
