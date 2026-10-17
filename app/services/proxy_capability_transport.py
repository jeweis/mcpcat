"""在公开传输边界收窄输入能力，不修改 SDK 的握手或私有分发器。"""

from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, AsyncIterator

from anyio.abc import ObjectSendStream
from fastmcp.client.transports import ClientTransport
from fastmcp.client.transports.base import TransportOptions
from mcp import ClientSession
from mcp.shared.message import SessionMessage
from mcp_types import CLIENT_CAPABILITIES_META_KEY, JSONRPCRequest

from app.services.proxy_logging import OrderedLogStream


class CapabilitySendStream(ObjectSendStream[SessionMessage]):
    """保留原消息及传输元数据，仅限制已存在的 elicitation 声明。"""

    def __init__(
        self, stream: Any, elicitation: dict | None, roots_changed: bool = False
    ) -> None:
        self.stream = stream
        self.elicitation = elicitation
        self.roots_changed = roots_changed

    def restrict(self, capabilities: dict, can_notify: bool = True) -> dict:
        """不增加 SDK 未声明的能力，保留其他能力字段。"""
        result = dict(capabilities)
        if "roots" in result:
            result["roots"] = {
                **result["roots"],
                "listChanged": self.roots_changed and can_notify,
            }
        if "elicitation" in result:
            if self.elicitation is None:
                result.pop("elicitation")
            else:
                result["elicitation"] = dict(self.elicitation)
        return result

    async def send(self, item: SessionMessage) -> None:
        """复制待发送请求，避免修改 SDK 缓存或其他会话的声明。"""
        request = item.message
        if isinstance(request, JSONRPCRequest) and request.params is not None:
            params = dict(request.params)
            if request.method == "initialize" and "capabilities" in params:
                params["capabilities"] = self.restrict(params["capabilities"])
            meta = params.get("_meta")
            if isinstance(meta, dict) and CLIENT_CAPABILITIES_META_KEY in meta:
                params["_meta"] = {
                    **meta,
                    CLIENT_CAPABILITIES_META_KEY: self.restrict(
                        meta[CLIENT_CAPABILITIES_META_KEY], can_notify=False
                    ),
                }
            item = replace(
                item,
                message=request.model_copy(update={"params": params}),
            )
        await self.stream.send(item)

    async def aclose(self) -> None:
        """将关闭交回原 SDK 传输。"""
        await self.stream.aclose()


class CapabilityTransport(ClientTransport):
    """复用原传输和 ProxySession，仅包装公开发送流。"""

    def __init__(
        self,
        transport: ClientTransport,
        elicitation: dict | None,
        roots_watch: Any = None,
        ordered_log_handler: Any = None,
    ) -> None:
        self.transport = transport
        self.elicitation = elicitation
        self.legacy_only = transport.legacy_only
        self.roots_watch = roots_watch
        self.ordered_log_handler = ordered_log_handler

    @asynccontextmanager
    async def connect_session(
        self, *, transport_options: TransportOptions | None = None, **kwargs: Any
    ) -> AsyncIterator[ClientSession]:
        """保留调用者指定的会话类及全部传输选项。"""
        options = transport_options or TransportOptions()
        elicitation = self.elicitation
        roots_watch = self.roots_watch
        ordered_log_handler = self.ordered_log_handler

        class ScopedSession(options.session_class):
            def __init__(self, read_stream, write_stream, **session_kwargs):
                if ordered_log_handler is not None:
                    read_stream = OrderedLogStream(read_stream, ordered_log_handler)
                super().__init__(
                    read_stream,
                    CapabilitySendStream(
                        write_stream, elicitation, roots_watch is not None
                    ),
                    **session_kwargs,
                )

        async with self.transport.connect_session(
            transport_options=replace(options, session_class=ScopedSession), **kwargs
        ) as session:
            if roots_watch is None:
                yield session
            else:
                async with roots_watch(session):
                    yield session

    async def close(self) -> None:
        """保留原传输的子进程与连接清理行为。"""
        await self.transport.close()

    def get_session_id(self) -> str | None:
        """返回原传输的会话标识。"""
        return self.transport.get_session_id()
