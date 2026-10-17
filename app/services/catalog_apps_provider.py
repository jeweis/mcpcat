"""Catalog 的已托管 Apps 入口；复用原 Registry，不另建发布/权限状态。"""

from collections.abc import Sequence
from typing import Any

from fastmcp.apps.config import is_model_visible
from fastmcp.resources import Resource
from fastmcp.server.providers import Provider
from fastmcp.tools import Tool
from fastmcp.utilities.versions import VersionSpec

from app.services.app_resources_provider import AppResourcesProvider
from app.services.app_tools_provider import AppToolsProvider


class CatalogAppsProvider(Provider):
    """原 Apps 开关、公开审核、绑定和逐次 Key 校验在 Catalog 入口仍生效。"""

    def __init__(self, manager: Any) -> None:
        super().__init__()
        self.manager = manager

    def _runtime(self) -> Any:
        runtime = getattr(self.manager, "apps_runtime", None)
        return runtime if runtime is not None and runtime.available() else None

    async def _list_tools(self) -> Sequence[Tool]:
        """只在模型目录展示打开动作，不把 UI-only 后端纳入普通搜索。"""
        runtime = self._runtime()
        if runtime is None:
            return []
        tools = await AppToolsProvider(runtime.registry, runtime.resolver).list_tools()
        return [tool for tool in tools if is_model_visible(tool)]

    async def _get_tool(
        self, name: str, version: VersionSpec | None = None
    ) -> Tool | None:
        """回调仍可直接调用，但每次由 App Registry/Resolver 重新授权。"""
        runtime = self._runtime()
        if runtime is None:
            return None
        return await AppToolsProvider(runtime.registry, runtime.resolver).get_tool(
            name, version
        )

    async def _list_resources(self) -> Sequence[Resource]:
        """同一不可变 UI/附件可从 Catalog 取得，不复制或重写原制品。"""
        runtime = self._runtime()
        if runtime is None:
            return []
        return await AppResourcesProvider(runtime.registry).list_resources()

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        """URI 仍固定 Apps 身份与版本，不能用 Catalog 改写为另一个来源。"""
        runtime = self._runtime()
        if runtime is None:
            return None
        return await AppResourcesProvider(runtime.registry).get_resource(uri, version)
