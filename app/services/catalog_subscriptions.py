"""Catalog 的平台提交事件按真实对外目录过滤，不广播内部管理索引。"""

from contextlib import asynccontextmanager
from typing import Any

from fastmcp.server.providers import FastMCPProvider
from mcp.shared.subscriptions import ResourceUpdated

from app.services.catalog_subscription_sources import CatalogSourceChanges
from app.services.registry_subscriptions import RegistrySubscriptions, VisibleSnapshot
from app.services.subscription_context import subscription_snapshot_read


class CatalogSubscriptions(RegistrySubscriptions):
    """使用经过 Search Transform/Apps/Skills Provider 的最终目录。"""

    def __init__(self, attachment: Any, sources: Any) -> None:
        self.attachment = attachment
        self.sources = sources
        super().__init__(
            attachment.runtime.database,
            self.catalog_snapshot,
            attachment.subscription_authorized,
            supported=frozenset({"resources", "tools"}),
            principal=attachment.runtime.principal,
            legacy_schedule=attachment.runtime.manager.lifecycle.schedule,
        )

    async def catalog_snapshot(self) -> VisibleSnapshot:
        """内部目录读取抑制监听重入，仍保留全部身份及可见性过滤。"""
        with subscription_snapshot_read():
            provider = FastMCPProvider(self.attachment.server)
            resources = await provider.list_resources()
            tools = await provider.list_tools()
        return VisibleSnapshot.from_components(resources, tools)

    @asynccontextmanager
    async def watch_changes(self, uris: Any):
        """合并实际事务、来源生命周期及上游 MCP 事件。"""
        async with CatalogSourceChanges(self, self.sources(), uris).watch() as changes:
            yield changes

    async def events_for_change(self, previous, current, uris, change) -> list:
        """来源资源更新保留映射身份，发送前再次查实际资源资格。"""
        events = self.changed_events(previous, current, uris)
        if isinstance(change, ResourceUpdated) and change.uri in uris:
            with subscription_snapshot_read():
                resource = await FastMCPProvider(self.attachment.server).get_resource(
                    change.uri
                )
            if resource is not None and change not in events:
                events.append(change)
        return events
