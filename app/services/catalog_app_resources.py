"""来源资源的派生 UI 装配；非 UI 字节、MIME 和 metadata 不改写。"""

import asyncio

from fastmcp.apps import UI_MIME_TYPE
from fastmcp.resources import ResourceContent, ResourceResult
from fastmcp.server.providers import FastMCPProvider
from fastmcp.tools import Tool

from app.services.catalog_app_relay import relay_document, relay_metadata
from app.services.catalog_app_sources import CatalogAppSource
from app.services.catalog_app_tools import callback_bindings


async def resource_description(source: CatalogAppSource, original: str) -> dict:
    """读取公开资源描述，动态 template 仍可经 read_resource 按需读取。"""
    resource = await asyncio.wait_for(
        FastMCPProvider(source.server).get_resource(original), 30
    )
    if resource is None:
        return {"name": original}
    meta = resource.get_meta()
    if resource.mime_type == UI_MIME_TYPE:
        meta, _, _ = relay_metadata(meta)
    return {
        "name": resource.name,
        "title": resource.title,
        "icons": resource.icons,
        "tags": resource.tags,
        "annotations": resource.annotations,
        "version": resource.version,
        "description": resource.description,
        "mime_type": resource.mime_type,
        "meta": meta,
    }


def relay_result(
    source: CatalogAppSource, result: ResourceResult, tools: list[Tool]
) -> ResourceResult:
    """原始 UI 字节单独摘要；只派生 UI 入口，不重写资源或用户数据正文。"""
    contents = []
    bindings = callback_bindings(source, tools)
    for item in result.contents:
        if item.mime_type != UI_MIME_TYPE:
            contents.append(item)
            continue
        data = item.content.encode() if isinstance(item.content, str) else item.content
        document = relay_document(
            source.identity, data, item.meta or {}, bindings, title=source.name
        )
        contents.append(ResourceContent(document.html, UI_MIME_TYPE, document.metadata))
    return ResourceResult(contents, meta=result.meta)
