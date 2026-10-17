"""Catalog 上游 Apps 独立 Provider；来源 URI/回调不受 Search Transform 改写。"""

import asyncio
from collections.abc import Sequence
from typing import Any

from fastmcp.exceptions import ResourceError
from fastmcp.resources import Resource, ResourceResult
from fastmcp.server.providers import Provider
from fastmcp.tools import Tool

from app.models.mcp_config import CatalogConfig
from app.services.catalog_app_identity import PREFIX, decode_catalog_resource
from app.services.catalog_app_operations import source_operation
from app.services.catalog_app_relay import CALLBACK
from app.services.catalog_app_request import authorize_catalog_source
from app.services.catalog_app_resources import relay_result, resource_description
from app.services.catalog_app_sources import CatalogAppSource, CatalogAppSources
from app.services.catalog_app_tools import (
    app_uri,
    model_app,
    source_tool,
    source_tools,
)
from app.services.catalog_provider import CatalogProvider, make_namespaced_name


class CatalogUpstreamAppsProvider(Provider):
    """只在 Apps 显式启用时聚合；源对象、当前 Key 和发布配置逐次检查。"""

    def __init__(self, manager: Any, directory: CatalogProvider | None = None) -> None:
        super().__init__()
        self.manager = manager
        self.directory = directory or CatalogProvider(manager, CatalogConfig())
        self._sources: CatalogAppSources | None = None

    def sources(self) -> CatalogAppSources | None:
        """初始化时可尚无 Apps runtime，实际请求不能假报可用。"""
        runtime = getattr(self.manager, "apps_runtime", None)
        if runtime is None or not runtime.available():
            return None
        if self._sources is None:
            self._sources = CatalogAppSources(
                runtime.database,
                self.manager,
                available=runtime.available,
                authorize=authorize_catalog_source,
            )
        return self._sources

    async def _list_tools(self) -> Sequence[Tool]:
        """只列模型可见打开动作，opaque 回调不进入模型搜索目录。"""
        sources = self.sources()
        if sources is None:
            return []
        safe_names = {tool.name for tool in await self.directory.unfiltered_tools()}
        result: dict[str, Tool] = {}
        origins: dict[str, CatalogAppSource] = {}
        conflicts: set[str] = set()
        for source in sources.sources():
            try:
                tools = await source_tools(sources, source)
                for tool in tools:
                    if model_app(tool):
                        wrapped = source_tool(sources, source, tool, callback=False)
                        if wrapped.name not in safe_names:
                            continue
                        if wrapped.name in result:
                            conflicts.add(wrapped.name)
                        result[wrapped.name] = wrapped
                        origins[wrapped.name] = source
            except Exception:
                # 目录故障不输出上游异常正文或凭证，也不执行其他来源兜底。
                continue
        return [
            tool
            for name, tool in result.items()
            if name not in conflicts and sources.current(origins[name])
        ]

    async def _get_tool(self, name: str, version=None) -> Tool | None:
        """猜到 callback 仍只在当前来源目录与授权范围中查找。"""
        sources = self.sources()
        if sources is None:
            return None
        if (
            not CALLBACK.fullmatch(name)
            and len(await self.directory.matching_tools(name, version)) != 1
        ):
            return None
        found = []
        for source in sources.sources():
            if not name.startswith(("catapp_", source.name + "__")):
                continue
            try:
                for tool in await source_tools(sources, source):
                    if version is not None and not version.matches(tool.version):
                        continue
                    callback = name == source.identity.callback(tool.name)
                    if callback or (
                        model_app(tool)
                        and name == make_namespaced_name(source.name, tool.name)
                    ):
                        found.append(
                            (
                                source,
                                source_tool(sources, source, tool, callback=callback),
                            )
                        )
            except Exception:
                continue
        return found[0][1] if len(found) == 1 and sources.current(found[0][0]) else None

    async def _get_resource(self, uri: str, version=None) -> Resource | None:
        """无需事先列目录；只解当前来源/代际，不回退其他 Provider。"""
        if not uri.startswith(PREFIX):
            return None
        sources = self.sources()
        if sources is None:
            return None
        try:
            identity, original = decode_catalog_resource(uri)
            source = next(
                (item for item in sources.sources() if item.identity == identity), None
            )
            if source is None:
                return None
            description = await resource_description(source, original)
            if not sources.current(source):
                return None
            return self._resource(sources, source, uri, original, description)
        except Exception:
            return None

    def _resource(
        self,
        sources: CatalogAppSources,
        source: CatalogAppSource,
        uri: str,
        original: str,
        description: dict,
    ) -> Resource:
        async def read() -> ResourceResult:
            try:
                tools = await source_tools(sources, source)
                result = await asyncio.wait_for(
                    source_operation(
                        sources, source, lambda: source.server.read_resource(original)
                    ),
                    30,
                )
                if not sources.current(source):
                    raise ValueError("source changed")
                return relay_result(source, result, tools)
            except Exception:
                raise ResourceError("Catalog App resource unavailable") from None

        return Resource.from_function(read, uri, **description)

    async def _list_resources(self) -> Sequence[Resource]:
        """只列当前可见 App UI，附件仍由来源固定 URI 按需读取。"""
        sources = self.sources()
        if sources is None:
            return []
        result = {}
        origins = {}
        for source in sources.sources():
            try:
                for tool in await source_tools(sources, source):
                    uri = app_uri(tool)
                    if model_app(tool) and uri:
                        wrapped = source.identity.resource(uri)
                        description = await resource_description(source, uri)
                        if sources.current(source):
                            result[wrapped] = self._resource(
                                sources, source, wrapped, uri, description
                            )
                            origins[wrapped] = source
            except Exception:
                continue
        return [
            resource
            for uri, resource in result.items()
            if sources.current(origins[uri])
        ]
