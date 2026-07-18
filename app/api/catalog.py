"""Catalog API - 工具搜索 MCP 状态、刷新与调用。"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()


class CatalogCallRequest(BaseModel):
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


def _get_catalog(request: Request):
    manager = getattr(request.app.state, "server_manager", None)
    if not manager or not manager.catalog_service:
        raise HTTPException(status_code=503, detail="Catalog 服务未初始化")
    return manager.catalog_service


@router.get("/catalog/status")
async def get_catalog_status(request: Request):
    catalog = _get_catalog(request)
    return catalog.get_catalog_status()


@router.post("/catalog/refresh")
async def refresh_catalog(request: Request):
    catalog = _get_catalog(request)
    await catalog.refresh_index()
    return {
        "message": "Catalog 目录刷新完成",
        "status": catalog.get_catalog_status(),
    }


@router.get("/catalog/search")
async def search_catalog(query: str, request: Request):
    catalog = _get_catalog(request)
    try:
        results = await catalog.search_tools(query)
    except Exception as exc:
        logger.exception("Catalog 搜索失败")
        raise HTTPException(
            status_code=500,
            detail=f"Catalog 搜索失败: {exc}",
        ) from exc
    return {
        "query": query,
        "total": len(results),
        "tools": results,
    }


@router.post("/catalog/call")
async def call_catalog(body: CatalogCallRequest, request: Request):
    catalog = _get_catalog(request)
    try:
        result = await catalog.call_tool(body.name, body.arguments)
    except Exception as exc:
        logger.exception("Catalog 工具调用失败: %s", body.name)
        raise HTTPException(
            status_code=400, detail=f"Catalog 工具调用失败: {exc}"
        ) from exc
    return {"result": result}
