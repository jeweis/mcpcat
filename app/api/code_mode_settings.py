"""管理员 CodeMode 设置；配置保存不等同于执行端点就绪。"""

from fastapi import APIRouter, HTTPException, Request

from app.models.code_mode_config import CodeModeConfig
from app.services.code_mode_settings import (
    code_mode_config,
    code_mode_dependency_versions,
    validate_code_mode_dependencies,
    validate_code_mode_path,
)
from app.services.config_service import ConfigService
from app.services.mcp_route_conflicts import mcp_route_occupied

router = APIRouter(prefix="/admin/settings/code-mode")


def _status(request: Request, config: CodeModeConfig) -> dict:
    runtime = getattr(request.app.state, "code_mode_runtime", None)
    ready = runtime is not None and runtime.available()
    return {
        "config": config.model_dump(include=set(CodeModeConfig.model_fields)),
        "ready": ready,
        "restart_required": not ready,
        "reason": None if ready else "endpoint_not_mounted",
        "dependency_versions": code_mode_dependency_versions(),
    }


@router.get("")
async def get_code_mode_settings(request: Request) -> dict:
    """AuthMiddleware 对 admin 路径强制 WRITE；不返回凭证。"""
    return _status(request, code_mode_config())


@router.put("")
async def update_code_mode_settings(body: CodeModeConfig, request: Request) -> dict:
    """部分更新保留未知根字段，冲突/非法限制不写入。"""
    current = code_mode_config().model_dump()
    updates = body.model_dump(exclude_unset=True)
    if "limits" in updates:
        updates["limits"] = {**current["limits"], **updates["limits"]}
    config = CodeModeConfig.model_validate({**current, **updates})
    try:
        validate_code_mode_dependencies()
        validate_code_mode_path(config, request.app.state.server_manager)
        runtime = getattr(request.app.state, "code_mode_runtime", None)
        owned = (
            runtime is not None
            and getattr(runtime, "http_app", None) is not None
            and runtime.config.path_name == config.path_name
        )
        if not owned and mcp_route_occupied(request.app, config.path_name):
            raise ValueError("CodeMode 路径与已挂载端点冲突")
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not ConfigService.update_setting_section("code_mode", config.model_dump()):
        raise HTTPException(status_code=500, detail="CodeMode 配置保存失败")
    runtime = getattr(request.app.state, "code_mode_runtime", None)
    if runtime is not None and config.model_dump() != current:
        await runtime.cancel_executions()
    return _status(request, config)
