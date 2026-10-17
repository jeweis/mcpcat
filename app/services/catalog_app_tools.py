"""来源内的 UI 回调映射，保留真实工具 Schema/结果，不猜全局同名工具。"""

import asyncio
import copy
from functools import partial

from fastmcp.apps.config import is_model_visible
from fastmcp.exceptions import ToolError
from fastmcp.server.providers import FastMCPProvider
from fastmcp.server.providers.addressing import (
    hash_tool,
    hashed_backend_name,
    tool_identity,
)
from fastmcp.tools import Tool, ToolResult
from fastmcp.utilities.versions import VersionSpec

from app.services.app_tools_provider import AppGatewayTool
from app.services.catalog_app_operations import source_operation
from app.services.catalog_app_sources import CatalogAppSource, CatalogAppSources
from app.services.catalog_provider import make_namespaced_name
from app.services.subscription_context import subscription_snapshot_read


def app_uri(tool: Tool) -> str | None:
    """只解释已知 UI resourceUri，未知 metadata 不猜作 App。"""
    meta = tool.get_meta()
    ui = meta.get("ui")
    value = ui.get("resourceUri") if isinstance(ui, dict) else None
    value = value or meta.get("ui/resourceUri")
    return value if isinstance(value, str) and value else None


async def source_tools(
    sources: CatalogAppSources, source: CatalogAppSource
) -> list[Tool]:
    """目录读取有界且两端检查当前来源/权限；不保留长期工具授权快照。"""
    if not sources.current(source):
        raise ValueError("Catalog App source unavailable")
    # 聚合内部发现不能被来源中间件当作下游客户端直接订阅。
    with subscription_snapshot_read():
        tools = list(
            await asyncio.wait_for(FastMCPProvider(source.server).list_tools(), 30)
        )
    if len(tools) > 4096 or not sources.current(source):
        raise ValueError("Catalog App source unavailable")
    return tools


def callback_bindings(source: CatalogAppSource, tools: list[Tool]) -> dict[str, str]:
    """真实名称和框架公开 hash 别名仅映射到同一来源的实际工具。"""
    bindings: dict[str, str] = {}
    conflicts: set[str] = set()
    for tool in tools:
        callback = source.identity.callback(tool.name)
        aliases = [tool.name]
        identity = tool_identity(tool)
        ui = tool.get_meta().get("ui", {})
        if identity and isinstance(ui, dict) and "app" in ui.get("visibility", []):
            app = tool.get_meta().get("fastmcp", {}).get("app")
            if isinstance(app, str):
                # Namespace 可以使用不同分隔符；不能仅切字符串猜原工具名。
                # 只有框架公开 hash 算法核对一致的原名才登记 hash 别名。
                aliases.extend(
                    hashed_backend_name(app, tool.name[index:])
                    for index in range(len(tool.name))
                    if hash_tool(app, tool.name[index:]) == identity
                )
        for alias in aliases:
            if alias in bindings and bindings[alias] != callback:
                conflicts.add(alias)
            bindings[alias] = callback
    for alias in conflicts:
        bindings.pop(alias, None)
    return bindings


def scoped_metadata(source: CatalogAppSource, tool: Tool, *, callback: bool) -> dict:
    """metadata 深拷贝，仅映射已知 UI 字段，不重写 Prefab/用户业务数据。"""
    meta = copy.deepcopy(tool.get_meta())
    # SDK 的全局 hash 查找不能带来源；Catalog 必须只接受自己的 opaque 回调。
    # 原 hash 只在当前来源的 callback_bindings 中作为输入别名使用。
    if isinstance(meta.get("fastmcp"), dict):
        meta["fastmcp"].pop("tool_hash", None)
    if callback:
        meta["ui"] = {"visibility": ["app"]}
        meta.pop("ui/resourceUri", None)
    else:
        uri = app_uri(tool)
        if uri:
            if isinstance(meta.get("ui"), dict) and "resourceUri" in meta["ui"]:
                meta["ui"]["resourceUri"] = source.identity.resource(uri)
            if "ui/resourceUri" in meta:
                meta["ui/resourceUri"] = source.identity.resource(uri)
    meta["io.mcpcat/gateway-component"] = "apps"
    return meta


async def _dispatch(
    sources: CatalogAppSources,
    source: CatalogAppSource,
    original: Tool,
    arguments: dict,
) -> ToolResult:
    version = VersionSpec(eq=original.version) if original.version else None
    if not sources.current(source):
        raise ToolError("Catalog App source unavailable")
    tool = await asyncio.wait_for(
        FastMCPProvider(source.server).get_tool(original.name, version), 30
    )
    if tool is None or not sources.current(source):
        raise ToolError("Catalog App source unavailable")
    if tool.parameters != original.parameters:
        raise ToolError("Catalog App tool schema changed; reopen the App")
    result = await asyncio.wait_for(
        source_operation(
            sources,
            source,
            lambda: source.server.call_tool(tool.name, arguments, version=version),
        ),
        30,
    )
    if not sources.current(source):
        raise ToolError("Catalog App source changed after dispatch; outcome unknown")
    return result


def source_tool(
    sources: CatalogAppSources,
    source: CatalogAppSource,
    original: Tool,
    *,
    callback: bool,
) -> Tool:
    """保留原始 Schema；运行时再次 lookup 并通过源服务器公开调用链分发。"""
    tool = AppGatewayTool(
        name=(
            source.identity.callback(original.name)
            if callback
            else make_namespaced_name(source.name, original.name)
        ),
        title=original.title,
        icons=original.icons,
        tags=original.tags,
        version=original.version,
        description=(
            original.description
            if callback
            else (original.description or "")
            + "\nInteractive UI requires an MCP Apps-capable host; the tool result remains available without UI."
        ),
        parameters=original.parameters,
        output_schema=original.output_schema,
        annotations=original.annotations,
        meta=scoped_metadata(source, original, callback=callback),
    )
    tool._dispatch = partial(_dispatch, sources, source, original)
    return tool


def model_app(tool: Tool) -> bool:
    """打开动作可见性不授予执行权限；UI-only 不能进入模型目录。"""
    return app_uri(tool) is not None and is_model_visible(tool)
