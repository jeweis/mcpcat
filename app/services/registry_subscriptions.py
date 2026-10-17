"""事务失效信号经当前身份可见快照比较后，生成正式 SDK 订阅事件。"""

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import anyio
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import Middleware
from mcp.server.context import ServerRequestContext
from mcp.server.subscriptions import ListenHandler
from mcp.shared.exceptions import MCPError
from mcp.shared.subscriptions import (
    ResourcesListChanged,
    ResourceUpdated,
    ToolsListChanged,
    PromptsListChanged,
)
from mcp.types import (
    INVALID_REQUEST,
    SubscriptionFilter,
    SubscriptionsListenRequestParams,
)
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from app.services.proxy_subscriptions import _ReadyBus
from app.storage.database import Database
from app.services.subscription_context import is_subscription_snapshot_read
from app.services.visible_subscription_snapshot import VisibleSnapshot


class RegistrySubscriptions(Middleware):
    def __init__(
        self,
        database: Database,
        snapshot: Callable[[], Awaitable[VisibleSnapshot]],
        authorized: Callable[[], bool],
        *,
        supported: frozenset[str] = frozenset({"resources"}),
        principal: Callable[[], str] | None = None,
        legacy_schedule: Callable | None = None,
    ) -> None:
        self.database, self.snapshot, self.authorized = database, snapshot, authorized
        self.supported = supported
        self.active = 0
        from app.services.legacy_registry_subscriptions import (
            LegacyRegistrySubscriptions,
        )

        self.legacy = (
            LegacyRegistrySubscriptions(self, principal, legacy_schedule)
            if principal is not None
            else None
        )

    @asynccontextmanager
    async def watch_changes(self, uris: Any):
        """默认只监听实际提交，派生端点可合并来源事件。"""
        async with self.database.changes.watch() as changes:
            yield changes

    async def events_for_change(
        self,
        previous: VisibleSnapshot,
        current: VisibleSnapshot,
        uris: Any,
        change: Any,
    ) -> list:
        """默认由可见快照差异生成事件。"""
        return self.changed_events(previous, current, uris)

    async def on_initialize(self, context: Any, call_next: Callable) -> Any:
        result = await call_next(context)
        if self.legacy is not None:
            from mcp.types import ResourcesCapability, ToolsCapability

            if "resources" in self.supported:
                result.capabilities.resources = ResourcesCapability(
                    subscribe=True, list_changed=True
                )
            if "tools" in self.supported:
                result.capabilities.tools = ToolsCapability(list_changed=True)
        return result

    @staticmethod
    def changed_events(
        previous: VisibleSnapshot, current: VisibleSnapshot, uris: Any
    ) -> list:
        """新旧协议共用可见快照差异，不通知已经不可见的 URI。"""
        events = []
        if current.resources != previous.resources:
            events.append(ResourcesListChanged())
        if current.tools != previous.tools:
            events.append(ToolsListChanged())
        if current.prompts != previous.prompts:
            events.append(PromptsListChanged())
        for uri in uris:
            if uri in current.resources and current.resources[
                uri
            ] != previous.resources.get(uri):
                events.append(ResourceUpdated(uri))
        return events

    async def on_request(self, context: Any, call_next: Callable) -> Any:
        rc = (
            context.fastmcp_context.request_context
            if context.fastmcp_context is not None
            else None
        )
        if (
            self.legacy is not None
            and rc is not None
            and rc.protocol_version not in MODERN_PROTOCOL_VERSIONS
        ):
            if context.method in {"resources/subscribe", "resources/unsubscribe"}:
                return await self.legacy.handle(context)
            result = await call_next(context)
            if not is_subscription_snapshot_read() and context.method in {
                "tools/list",
                "resources/list",
                "resources/templates/list",
                "prompts/list",
            }:
                try:
                    await self.legacy.handle(context)
                except MCPError:
                    # 通知监听达到限额/初始化失败不能使已成功的列表读取失败。
                    pass
            return result
        if context.method != "subscriptions/listen":
            return await call_next(context)
        rc = context.fastmcp_context.request_context
        if rc.protocol_version not in MODERN_PROTOCOL_VERSIONS:
            return await call_next(context)
        if self.active >= 32 or not self.authorized():
            raise MCPError(INVALID_REQUEST, "Subscription unavailable")
        request = get_http_request()
        envelope = await request.json()
        params = SubscriptionsListenRequestParams.model_validate(envelope["params"])
        requested = params.notifications
        uris = requested.resource_subscriptions or []
        if len(uris) > 32 or any(len(uri) > 4096 for uri in uris):
            raise MCPError(INVALID_REQUEST, "Resource subscription limit exceeded")
        honored = SubscriptionFilter(
            resources_list_changed=bool(
                requested.resources_list_changed and "resources" in self.supported
            ),
            tools_list_changed=bool(
                requested.tools_list_changed and "tools" in self.supported
            ),
            prompts_list_changed=bool(
                requested.prompts_list_changed and "prompts" in self.supported
            ),
            resource_subscriptions=uris if "resources" in self.supported else None,
        )
        sdk_context = ServerRequestContext(
            session=rc.session,
            lifespan_context=rc.lifespan_context,
            protocol_version=rc.protocol_version,
            method=context.method,
            request_id=envelope["id"],
            request=request,
        )
        self.active += 1
        try:
            async with self.watch_changes(uris) as changes:
                accepts = getattr(changes, "accepts", None)
                if accepts is not None:
                    honored.resource_subscriptions = [
                        uri for uri in uris if accepts(uri)
                    ] or None
                previous = await self.snapshot()
                bus = _ReadyBus()
                handler = ListenHandler(
                    bus, max_subscriptions=1, max_buffered_events=200
                )

                async def watch() -> None:
                    nonlocal previous
                    await bus.ready.wait()
                    while True:
                        change = await changes.get()
                        if not self.authorized():
                            group.cancel_scope.cancel()
                            return
                        current = await self.snapshot()
                        events = await self.events_for_change(
                            previous, current, uris, change
                        )
                        previous = current
                        for event in events:
                            if not self.authorized():
                                group.cancel_scope.cancel()
                                return
                            await bus.publish(event)

                async def guard() -> None:
                    while self.authorized():
                        await anyio.sleep(0.25)
                    group.cancel_scope.cancel()

                try:
                    async with anyio.create_task_group() as group:
                        group.start_soon(watch)
                        group.start_soon(guard)
                        try:
                            return await handler(
                                sdk_context,
                                params.model_copy(update={"notifications": honored}),
                            )
                        finally:
                            group.cancel_scope.cancel()
                finally:
                    handler.close()
                raise MCPError(INVALID_REQUEST, "Subscription access revoked")
        finally:
            self.active -= 1
