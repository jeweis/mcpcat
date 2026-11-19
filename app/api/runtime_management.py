"""管理员查看上游运行状态、依赖修订和原子切换缓存。"""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.api.diagnostic_logs import protect_config
from app.services.diagnostic_logs import diagnostic
from app.services.runtime_environments import RuntimeEnvironment

router = APIRouter()


class EnvironmentChange(BaseModel):
    """省略修订创建新环境；提供修订则回滚到已验证环境。"""

    revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


def runtime_snapshot(request: Request, name: str) -> dict:
    """不返回连接参数、环境变量和认证信息。"""
    info = request.app.state.server_manager.server_info.get(name)
    if info is None:
        raise HTTPException(404, "MCP 服务不存在")
    config = info.get("config") or {}
    result = {
        "name": name,
        "transport": config.get("type"),
        "status": info.get("status"),
        "protocol_mode": config.get("protocol_mode", "auto"),
    }
    if config.get("type") == "stdio":
        result["environment"] = RuntimeEnvironment(name, config).snapshot()
    else:
        result["environment"] = None
    store = request.app.state.diagnostic_logs
    protect_config(store)
    rows = store.query(server=name, limit=200)["items"]
    result["attempts"] = [
        row for row in rows if row.get("attempt_id") and row.get("source") != "stdio"
    ][:30]
    return result


@router.get("/admin/runtime")
async def list_runtime(request: Request, response: Response) -> dict:
    """列出已配置来源的管理员运行摘要。"""
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [
            runtime_snapshot(request, name)
            for name in request.app.state.server_manager.server_info
        ]
    }


@router.get("/admin/runtime/{name}")
async def get_runtime(name: str, request: Request, response: Response) -> dict:
    """按服务查询阶段、退出和依赖修订信息。"""
    response.headers["Cache-Control"] = "no-store"
    return runtime_snapshot(request, name)


@router.post("/admin/runtime/{name}/environment")
async def change_environment(
    name: str, body: EnvironmentChange, request: Request, response: Response
) -> dict:
    """切换后由下一次新连接使用，不中断已有业务请求或改写原始配置。"""
    manager = request.app.state.server_manager
    info = manager.server_info.get(name)
    if info is None:
        raise HTTPException(404, "MCP 服务不存在")
    config = info.get("config") or {}
    if config.get("type") != "stdio" or config.get("environment_mode") == "external":
        raise HTTPException(409, "此服务不使用平台托管的本地依赖环境")
    try:
        revision = RuntimeEnvironment(name, config).select(body.revision)
        diagnostic(
            "运行环境修订已切换",
            level="INFO",
            server=name,
            operation="environment",
            detail=("回滚" if body.revision else "创建新修订") + "：" + revision,
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    response.headers["Cache-Control"] = "no-store"
    return runtime_snapshot(request, name)
