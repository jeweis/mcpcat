"""新版模式生成 API；旧 /api/skills 结果结构和 local 默认不变。"""

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel

from app.api.skills import _actor
from app.services.mcp_skill_generator import generate_catalog_skill, generate_mcp_skill

router = APIRouter(prefix="/skill-variants/generate")


class GenerateModesRequest(BaseModel):
    """模式选择表示整批审核，不自动发布。"""

    selection: Literal["local", "remote", "both"] = "local"


@router.post("/service/{server_name}")
async def generate_service_modes(
    server_name: str,
    request: Request,
    body: GenerateModesRequest = Body(default_factory=GenerateModesRequest),
) -> dict:
    """来源运行中才能生成；remote 不要求 public_base_url。"""
    try:
        result = await generate_mcp_skill(
            request.app.state.database,
            manager=request.app.state.server_manager,
            server_name=server_name,
            actor=_actor(request),
            fallback_base_url=str(request.base_url).rstrip("/"),
            selection=body.selection,
        )
        return {
            **asdict(result),
            "selection": body.selection,
            "status": "draft" if result.changed else "unchanged",
        }
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/catalog")
async def generate_catalog_modes(
    request: Request,
    body: GenerateModesRequest = Body(default_factory=GenerateModesRequest),
) -> dict:
    """只绑定 Catalog 自己的搜索/调用工作流，不绑定用户服务同名工具。"""
    try:
        result = await generate_catalog_skill(
            request.app.state.database,
            manager=request.app.state.server_manager,
            actor=_actor(request),
            fallback_base_url=str(request.base_url).rstrip("/"),
            selection=body.selection,
        )
        return {
            **asdict(result),
            "selection": body.selection,
            "status": "draft" if result.changed else "unchanged",
        }
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
