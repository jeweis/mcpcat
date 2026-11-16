"""管理员网络诊断接口；认证头沿用应用配置，不在 URL 传递密钥。"""

import shutil

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.diagnostic_logs import protect_config
from app.services.network_command_runner import NetworkCommandRunner
from app.services.network_commands import NetworkCommand, command_args

router = APIRouter()


@router.get("/admin/commands")
async def command_capabilities(request: Request) -> dict:
    """告知执行位置、能力和限制；/api/admin 强制 WRITE 权限。"""
    return {
        "location": "MCPCat 所在服务器/容器",
        "commands": ["ping", "dns", "tcp", "http"],
        "ping_available": shutil.which("ping") is not None,
        "timeout_max": 60,
        "output_limit": 65536,
        "running": request.app.state.network_commands.running,
    }


@router.post("/admin/commands/run")
async def run_command(command: NetworkCommand, request: Request) -> StreamingResponse:
    """只执行白名单诊断，输出脱敏后流式返回；断开即取消。"""
    try:
        args = command_args(command)
    except FileNotFoundError as error:
        raise HTTPException(409, str(error)) from error
    store = request.app.state.diagnostic_logs
    protect_config(store)
    runner: NetworkCommandRunner = request.app.state.network_commands
    return StreamingResponse(
        runner.events(command, args),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )
