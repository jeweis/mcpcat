"""Apps 远程设置与真实挂载状态，默认不公开上传内容。"""

from fastapi import APIRouter, HTTPException, Request

from app.models.apps_mcp_config import AppsMCPConfig
from app.services.apps_mcp_settings import apps_mcp_config, validate_apps_path
from app.services.config_service import ConfigService
from app.services.mcp_route_conflicts import mcp_route_occupied

router = APIRouter(prefix="/admin/settings/apps-mcp")


def _status(request: Request, config: AppsMCPConfig) -> dict:
    runtime = getattr(request.app.state, "apps_mcp_runtime", None)
    ready = runtime is not None and runtime.available()
    return {
        "config": config.model_dump(include=set(AppsMCPConfig.model_fields)),
        "ready": ready,
        "restart_required": not ready,
        "reason": None if ready else "endpoint_not_mounted",
        "management_enabled": True,
    }


@router.get("")
async def get_apps_mcp_settings(request: Request) -> dict:
    """admin 由中间件强制 WRITE；不回显未知配置或凭证。"""
    return _status(request, apps_mcp_config())


@router.put("")
async def update_apps_mcp_settings(body: AppsMCPConfig, request: Request) -> dict:
    """部分更新保留旧未知配置；启用/改路径并不假装已经挂载。"""
    current = apps_mcp_config().model_dump()
    config = AppsMCPConfig.model_validate(
        {**current, **body.model_dump(exclude_unset=True)}
    )
    try:
        validate_apps_path(config, request.app.state.server_manager)
        runtime = getattr(request.app.state, "apps_mcp_runtime", None)
        owned = (
            runtime is not None
            and runtime.http_app is not None
            and runtime.config.path_name == config.path_name
        )
        if not owned and mcp_route_occupied(request.app, config.path_name):
            raise ValueError("Apps 路径与已挂载端点冲突")
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not ConfigService.update_setting_section("apps", config.model_dump()):
        raise HTTPException(status_code=500, detail="Apps 配置保存失败")
    return _status(request, config)
