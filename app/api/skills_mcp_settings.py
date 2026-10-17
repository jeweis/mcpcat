"""管理员远程 Skills 设置：保存配置与实际端点就绪状态明确分开。"""

from fastapi import APIRouter, HTTPException, Request

from app.models.skills_mcp_config import SkillsMCPConfig
from app.services.config_service import ConfigService
from app.services.mcp_route_conflicts import mcp_route_occupied
from app.services.skills_mcp_runtime import (
    skills_mcp_config,
    validate_skills_endpoint_path,
)

router = APIRouter(prefix="/admin/settings/skills-mcp")


def _status(request: Request, config: SkillsMCPConfig) -> dict:
    runtime = request.app.state.skills_mcp_runtime
    restart_required = bool(
        runtime.http_app is None
        or runtime.config.path_name != config.path_name
        or runtime.config.page_size != config.page_size
    )
    return {
        "config": config.model_dump(),
        "ready": runtime.available(),
        "restart_required": restart_required,
    }


@router.get("")
async def get_skills_mcp_settings(request: Request) -> dict:
    """AuthMiddleware 对管理员路径强制 WRITE，响应不包含凭证。"""
    return _status(request, skills_mcp_config())


@router.put("")
async def update_skills_mcp_settings(body: SkillsMCPConfig, request: Request) -> dict:
    """部分更新保留未知配置；冲突失败不得写入或覆盖路由。"""
    current = skills_mcp_config()
    config = SkillsMCPConfig.model_validate(
        {**current.model_dump(), **body.model_dump(exclude_unset=True)}
    )
    try:
        validate_skills_endpoint_path(config, request.app.state.server_manager)
        runtime = request.app.state.skills_mcp_runtime
        owned = (
            runtime.http_app is not None
            and config.path_name == runtime.config.path_name
        )
        if not owned and mcp_route_occupied(request.app, config.path_name):
            raise ValueError("Skills MCP 路径与已挂载端点冲突")
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not ConfigService.update_setting_section("skills_mcp", config.model_dump()):
        raise HTTPException(status_code=500, detail="远程 Skills 配置保存失败")
    return _status(request, config)
