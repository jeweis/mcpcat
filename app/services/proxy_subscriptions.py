"""通过公开 SDK 接口代理现代订阅；每条下游流拥有独立上游连接。"""

from collections.abc import Callable
from typing import Any

import anyio
from fastmcp import Client
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import Middleware
from mcp.server.context import ServerRequestContext
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, INVALID_REQUEST, SubscriptionsListenRequestParams
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
from starlette.requests import Request

from app.services.upstream_subscriptions import LegacyEvents, upstream_events
from app.services.legacy_resource_subscriptions import LegacyResourceSubscriptions
from app.services.subscription_context import is_subscription_snapshot_read


class _ReadyBus(InMemorySubscriptionBus):
    def __init__(self) -> None:
        super().__init__()
        self.ready = anyio.Event()

    def subscribe(self, listener: Callable) -> Callable:
        unsubscribe = super().subscribe(listener)
        self.ready.set()
        return unsubscribe


class ProxySubscriptions(Middleware):
    """来源和授权谓词必须由网关装配层绑定；不共享用户事件总线。"""

    def __init__(
        self,
        client_factory: Callable[..., Client],
        authorized: Callable[[Request], bool],
        *,
        max_streams: int = 32,
    ) -> None:
        self.client_factory = client_factory
        self.authorized = authorized
        self.max_streams = max_streams
        self.active = 0
        self.legacy_resources = None
        self.events_factory = upstream_events

    def attach_registry(self, attachment: Any) -> None:
        """同一来源只安装一条订阅入口，平台和来源事件在流内合并。"""
        from app.services.bound_skill_subscriptions import bound_skill_events

        self.events_factory = bound_skill_events(attachment)
        if self.legacy_resources is not None:
            self.legacy_resources.events_factory = self.events_factory

    async def on_initialize(self, context: Any, call_next: Callable) -> Any:
        result = await call_next(context)
        if self.legacy_resources is not None:
            from mcp.types import ResourcesCapability

            if result.capabilities.resources is None:
                result.capabilities.resources = ResourcesCapability()
            result.capabilities.resources.subscribe = True
        return result

    async def on_request(self, context: Any, call_next: Callable) -> Any:
        if (
            context.method in {"resources/subscribe", "resources/unsubscribe"}
            and self.legacy_resources is not None
        ):
            rc = context.fastmcp_context.request_context
            if rc.protocol_version not in MODERN_PROTOCOL_VERSIONS:
                return await self.legacy_resources.handle(context, get_http_request())
        if context.method != "subscriptions/listen":
            result = await call_next(context)
            request_context = (
                context.fastmcp_context.request_context
                if context.fastmcp_context is not None
                else None
            )
            if (
                self.legacy_resources is not None
                and not is_subscription_snapshot_read()
                and context.method
                in {
                    "tools/list",
                    "resources/list",
                    "resources/templates/list",
                    "prompts/list",
                }
                and request_context is not None
                and request_context.protocol_version not in MODERN_PROTOCOL_VERSIONS
            ):
                await self.legacy_resources.watch_lists(context, get_http_request())
            return result
        rc = context.fastmcp_context.request_context
        if rc.protocol_version not in MODERN_PROTOCOL_VERSIONS:
            return await call_next(context)
        request = get_http_request()
        if not self.authorized(request):
            raise MCPError(INVALID_REQUEST, "Subscription access denied")
        if self.active >= self.max_streams:
            raise MCPError(INVALID_REQUEST, "Subscription limit reached")
        envelope = await request.json()
        params = SubscriptionsListenRequestParams.model_validate(envelope["params"])
        uris = params.notifications.resource_subscriptions or []
        if len(uris) > 32 or any(len(uri) > 4096 for uri in uris):
            raise MCPError(INVALID_PARAMS, "Resource subscription limit exceeded")
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
            return await self._serve(request, sdk_context, params)
        finally:
            self.active -= 1

    async def _serve(
        self,
        request: Request,
        context: ServerRequestContext,
        params: SubscriptionsListenRequestParams,
    ) -> Any:
        legacy = LegacyEvents()
        async with legacy.send, legacy.receive, self.client_factory(
            message_handler=legacy
        ) as client:
            async with self.events_factory(
                client, params.notifications, legacy
            ) as stream:
                if not self.authorized(request):
                    raise MCPError(INVALID_REQUEST, "Subscription access revoked")
                # 只能承诺来源实际确认的范围，不能回显全部请求过滤器。
                honored = params.model_copy(update={"notifications": stream.honored})
                bus = _ReadyBus()
                handler = ListenHandler(
                    bus, max_subscriptions=1, max_buffered_events=200
                )

                async def forward() -> None:
                    await bus.ready.wait()
                    try:
                        async for event in stream:
                            if not self.authorized(request):
                                group.cancel_scope.cancel()
                                return
                            await bus.publish(event)
                    finally:
                        handler.close()

                async def guard() -> None:
                    while self.authorized(request):
                        await anyio.sleep(0.25)
                    # 撤权不能继续 drain 缓冲区中的旧消息。
                    group.cancel_scope.cancel()

                try:
                    async with anyio.create_task_group() as group:
                        group.start_soon(forward)
                        group.start_soon(guard)
                        try:
                            return await handler(context, honored)
                        finally:
                            group.cancel_scope.cancel()
                finally:
                    handler.close()
                raise MCPError(INVALID_REQUEST, "Subscription access revoked")


def attach_proxy_subscriptions(
    manager: Any, name: str, server: Any, config: dict
) -> ProxySubscriptions | None:
    """装配当前服务代际与实时 READ 鉴权，不转发平台凭证给来源。"""
    from app.models.mcp_config import PermissionType
    from app.services.proxy_clients import make_proxy_client_factory
    from app.services.security_service import security_service
    from app.services.legacy_input_bridge import LegacyInputBridge
    from app.services.modern_input_bridge import ModernInputBridge
    from app.services.roots_notifications import RootsNotifications

    if config.get("type") not in {"stdio", "sse", "streamable-http"}:
        return

    def authorized(request: Request) -> bool:
        info = manager.server_info.get(name)
        if (
            info is None
            or info.get("mcp") is not server
            or info.get("status") != "running"
        ):
            return False
        key = request.headers.get(security_service.get_auth_header_name())
        if not key:
            return not info["config"].get("require_auth", True)
        identity = security_service.verify_api_key(key) if key else None
        return bool(
            identity and security_service.has_permission(identity, PermissionType.READ)
        )

    for middleware in server.middleware:
        if isinstance(
            middleware, (LegacyInputBridge, ModernInputBridge, RootsNotifications)
        ):
            middleware.authorized = authorized

    factory = make_proxy_client_factory(config)
    adapter = ProxySubscriptions(factory, authorized)
    adapter.legacy_resources = LegacyResourceSubscriptions(
        factory,
        authorized,
        lambda job: manager.lifecycle.schedule(job, name),
        lambda request: request.headers.get(
            security_service.get_auth_header_name(), ""
        ),
    )
    server.add_middleware(adapter)
    return adapter
