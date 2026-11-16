"""管理员自由命令与网络诊断；认证头沿用应用配置，不在 URL 传递密钥。"""

import os
import shutil
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from app.api.diagnostic_logs import protect_config
from app.services.network_command_runner import NetworkCommandRunner
from app.services.network_commands import NetworkCommand, command_args
from app.services.shell_commands import ShellCommand

router = APIRouter()


@router.get("/admin/commands")
async def command_capabilities(request: Request) -> dict:
    """告知执行位置、能力和限制；/api/admin 强制 WRITE 权限。"""
    return {
        "location": "MCPCat 所在服务器/容器",
        "shell": "cmd.exe" if os.name == "nt" else "/bin/sh",
        "working_directory": os.getcwd(),
        "commands": ["shell", "ping", "dns", "tcp", "http"],
        "ping_available": shutil.which("ping") is not None,
        "timeout_max": 60,
        "output_limit": 65536,
        "running": request.app.state.network_commands.running,
    }


@router.post("/admin/commands/run")
async def run_command(
    command: Annotated[NetworkCommand | ShellCommand, Field(discriminator="kind")],
    request: Request,
) -> StreamingResponse:
    """管理员执行自由命令或网络快捷项，流式返回输出；断开即取消。"""
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
