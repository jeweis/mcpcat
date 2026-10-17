"""Catalog 上游事件监听：复用来源身份、公开客户端与有界生命周期。"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import anyio
from fastmcp.server.providers.proxy import FastMCPProxy
from mcp.shared.exceptions import MCPError
from mcp.shared.subscriptions import ResourceUpdated
from mcp.types import INVALID_REQUEST, METHOD_NOT_FOUND, SubscriptionFilter

from app.services.async_compat import cancel_task_once
from app.services.catalog_app_identity import PREFIX, decode_catalog_resource
from app.services.config_service import ConfigService
from app.services.proxy_clients import make_proxy_client_factory
from app.services.upstream_subscriptions import LegacyEvents, upstream_events


@dataclass
class SourceListener:
    source: Any
    config: dict
    uris: tuple[str, ...]
    ready: asyncio.Future
    task: asyncio.Task | None = None
    honored: tuple[str, ...] = ()


class CatalogSourceChanges:
    """每条下游订阅独立拥有来源连接，最多 32 个来源与 200 条事件。"""

    def __init__(self, owner: Any, sources: Any, uris: Any) -> None:
        self.owner, self.sources, self.uris = owner, sources, uris
        self.queue = asyncio.Queue(maxsize=200)
        self.wake = asyncio.Event()
        self.listeners: dict[str, SourceListener] = {}
        self.waiters = []

    def emit(self, change: Any) -> None:
        """溢出结束整个流，不能丢通知后继续假装完整。"""
        try:
            self.queue.put_nowait(change)
        except asyncio.QueueFull:
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(
                MCPError(INVALID_REQUEST, "Catalog event buffer exceeded")
            )

    async def get(self) -> Any:
        """供 Registry 共享的新旧协议消费者读取。"""
        change = await self.queue.get()
        if isinstance(change, Exception):
            raise change
        return change

    async def refresh(self) -> None:
        """旧客户端增删 URI 时唤醒范围协调，不轮询资源正文。"""
        if len(self.waiters) >= 32:
            raise MCPError(
                INVALID_REQUEST, "Catalog subscription update limit exceeded"
            )
        future = asyncio.get_running_loop().create_future()
        self.waiters.append(future)
        self.wake.set()
        try:
            await future
        finally:
            if future in self.waiters:
                self.waiters.remove(future)

    def accepts(self, uri: str) -> bool:
        """远程映射 URI 必须由对应来源真实确认，不能盲目回显请求。"""
        if not uri.startswith(PREFIX):
            return True
        try:
            identity, original = decode_catalog_resource(uri)
        except ValueError:
            return False
        return any(
            entry.source.identity == identity
            and original in entry.honored
            and self.sources.current(entry.source)
            for entry in self.listeners.values()
        )

    def source_uris(self, source: Any) -> tuple[str, ...]:
        result = []
        for uri in self.uris:
            if not uri.startswith(PREFIX):
                continue
            try:
                identity, original = decode_catalog_resource(uri)
            except ValueError:
                continue
            if identity == source.identity:
                result.append(original)
        return tuple(sorted(set(result)))

    async def stop(self, entry: SourceListener) -> None:
        if entry.task is not None:
            cancel_task_once(entry.task)
            await asyncio.gather(entry.task, return_exceptions=True)

    async def reconcile(self) -> None:
        sources = self.sources.sources() if self.sources is not None else []
        desired = {}
        for source in sources:
            config = ConfigService.get_server_config(source.name) or {}
            if isinstance(source.server, FastMCPProxy) and config.get("type") in {
                "stdio",
                "sse",
                "streamable-http",
            }:
                desired[source.name] = (source, config, self.source_uris(source))
        if len(desired) > 32:
            raise MCPError(
                INVALID_REQUEST, "Catalog subscription source limit exceeded"
            )
        for name, entry in list(self.listeners.items()):
            target = desired.get(name)
            if target is None or (
                entry.source != target[0]
                or entry.config != target[1]
                or entry.uris != target[2]
            ):
                await self.stop(entry)
                self.listeners.pop(name, None)
        for name, (source, config, uris) in desired.items():
            if name not in self.listeners:
                entry = SourceListener(
                    source, config, uris, asyncio.get_running_loop().create_future()
                )
                entry.ready.add_done_callback(
                    lambda result: None if result.cancelled() else result.exception()
                )
                self.listeners[name] = entry
                entry.task = asyncio.create_task(self.listen(entry))
        if self.listeners:
            await asyncio.wait_for(
                asyncio.gather(
                    *(asyncio.shield(entry.ready) for entry in self.listeners.values())
                ),
                10,
            )

    async def listen(self, entry: SourceListener) -> None:
        legacy = LegacyEvents()
        try:
            factory = make_proxy_client_factory(entry.config)
            async with legacy.send, legacy.receive, factory(
                message_handler=legacy
            ) as client:
                requested = SubscriptionFilter(
                    tools_list_changed=True,
                    resources_list_changed=True,
                    resource_subscriptions=list(entry.uris),
                )
                async with upstream_events(client, requested, legacy) as events:
                    entry.honored = tuple(events.honored.resource_subscriptions or [])
                    entry.ready.set_result(True)
                    async for event in events:
                        if not self.owner.authorized() or not self.sources.current(
                            entry.source
                        ):
                            return
                        if isinstance(event, ResourceUpdated):
                            if event.uri in entry.uris:
                                self.emit(
                                    ResourceUpdated(
                                        entry.source.identity.resource(event.uri)
                                    )
                                )
                        else:
                            self.emit(frozenset({"upstream"}))
                    self.emit(
                        MCPError(INVALID_REQUEST, "Catalog source subscription ended")
                    )
        except Exception as error:
            unsupported = isinstance(error, MCPError) and error.code == METHOD_NOT_FOUND
            if not entry.ready.done():
                if unsupported:
                    entry.ready.set_result(False)
                else:
                    entry.ready.set_exception(
                        MCPError(
                            INVALID_REQUEST, "Catalog source subscription unavailable"
                        )
                    )
            elif not unsupported:
                self.emit(
                    MCPError(INVALID_REQUEST, "Catalog source subscription failed")
                )
        finally:
            if not entry.ready.done():
                entry.ready.cancel()

    async def coordinate(self) -> None:
        batch = []
        try:
            while True:
                await self.wake.wait()
                self.wake.clear()
                batch, self.waiters = self.waiters, []
                await self.reconcile()
                for waiter in batch:
                    if not waiter.done():
                        waiter.set_result(None)
                self.emit(frozenset({"catalog_sources"}))
        except Exception:
            self.emit(MCPError(INVALID_REQUEST, "Catalog source update failed"))
            for waiter in [*batch, *self.waiters]:
                if not waiter.done():
                    waiter.set_exception(
                        MCPError(INVALID_REQUEST, "Catalog source update failed")
                    )
            self.waiters.clear()

    async def relay_changes(self, queue: Any) -> None:
        while True:
            await queue.get()
            self.wake.set()

    @asynccontextmanager
    async def watch(self):
        manager = self.owner.attachment.runtime.manager
        async with self.owner.database.changes.watch() as commits, manager.runtime_changes.watch() as runtime:
            tasks = []
            try:
                tasks = [
                    asyncio.create_task(self.relay_changes(commits)),
                    asyncio.create_task(self.relay_changes(runtime)),
                ]
                await self.reconcile()
                tasks.append(asyncio.create_task(self.coordinate()))
                yield self
            finally:
                all_tasks = [
                    *tasks,
                    *(
                        entry.task
                        for entry in self.listeners.values()
                        if entry.task is not None
                    ),
                ]
                for task in all_tasks:
                    cancel_task_once(task)
                # HTTP 取消作用域仍然生效，收尾必须屏蔽重复取消，且先取消全部子任务。
                with anyio.move_on_after(10, shield=True) as cleanup:
                    await asyncio.gather(*all_tasks, return_exceptions=True)
                for waiter in self.waiters:
                    waiter.cancel()
                if cleanup.cancel_called:
                    raise RuntimeError("Catalog subscriptions did not close")
