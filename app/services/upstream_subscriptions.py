"""旧通知到 SDK 事件的有限适配，不重写订阅协议传输。"""

from contextlib import asynccontextmanager
from typing import Any

import anyio
from fastmcp.client.messages import MessageHandler
from mcp.client.subscriptions import listen
from mcp.shared.exceptions import MCPError
from mcp.shared.subscriptions import (
    PromptsListChanged,
    ResourceUpdated,
    ResourcesListChanged,
    ToolsListChanged,
    event_matches,
)
from mcp.types import INVALID_REQUEST, SubscriptionFilter
from mcp_types.version import MODERN_PROTOCOL_VERSIONS


class LegacyEvents(MessageHandler):
    """不阻塞 SDK 接收循环；缓冲溢出或传输错误终止流。"""

    def __init__(self) -> None:
        self.honored = SubscriptionFilter()
        self.send, self.receive = anyio.create_memory_object_stream(200)

    async def on_notification(self, message: Any) -> None:
        constructors = {
            "notifications/tools/list_changed": ToolsListChanged,
            "notifications/resources/list_changed": ResourcesListChanged,
            "notifications/prompts/list_changed": PromptsListChanged,
        }
        if message.method == "notifications/resources/updated":
            event = ResourceUpdated(str(message.params.uri))
        elif message.method in constructors:
            event = constructors[message.method]()
        else:
            return
        if not event_matches(
            self.honored, frozenset(self.honored.resource_subscriptions or []), event
        ):
            return
        try:
            self.send.send_nowait(event)
        except (anyio.WouldBlock, anyio.ClosedResourceError):
            self.send.close()

    async def on_exception(self, message: Exception) -> None:
        self.send.close()

    def __aiter__(self):
        return self.receive.__aiter__()

    def close(self) -> None:
        self.send.close()
        self.receive.close()


@asynccontextmanager
async def upstream_events(client, requested: SubscriptionFilter, legacy: LegacyEvents):
    """确认成功的 URI 才计入订阅；异常和取消时清理已成功的部分。"""
    if client.protocol_version in MODERN_PROTOCOL_VERSIONS:
        async with listen(
            client.session,
            tools_list_changed=requested.tools_list_changed,
            prompts_list_changed=requested.prompts_list_changed,
            resources_list_changed=requested.resources_list_changed,
            resource_subscriptions=requested.resource_subscriptions or [],
        ) as stream:
            yield stream
        return
    caps = client.server_capabilities.model_dump(by_alias=True, exclude_none=True)
    uris = list(dict.fromkeys(requested.resource_subscriptions or []))
    if uris and not caps.get("resources", {}).get("subscribe"):
        raise MCPError(
            INVALID_REQUEST, "Upstream does not support resource subscriptions"
        )
    legacy.honored = SubscriptionFilter(
        tools_list_changed=bool(
            requested.tools_list_changed and caps.get("tools", {}).get("listChanged")
        ),
        resources_list_changed=bool(
            requested.resources_list_changed
            and caps.get("resources", {}).get("listChanged")
        ),
        prompts_list_changed=bool(
            requested.prompts_list_changed
            and caps.get("prompts", {}).get("listChanged")
        ),
        resource_subscriptions=uris or None,
    )
    subscribed = []
    try:
        for uri in uris:
            await client.session.subscribe_resource(uri)
            subscribed.append(uri)
        yield legacy
    finally:
        legacy.close()
        with anyio.move_on_after(5, shield=True):
            for uri in subscribed:
                try:
                    await client.session.unsubscribe_resource(uri)
                except Exception:
                    # 仍尝试清理其余资源；退出 Client 会关闭整个来源连接。
                    continue
