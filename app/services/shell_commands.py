"""管理员自由命令；每个请求使用独立的非交互系统 Shell。"""

import os
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ShellCommand(BaseModel):
    """管理员显式输入的完整脚本，支持管道、重定向及多行命令。"""

    kind: Literal["shell"]
    command: str = Field(min_length=1, max_length=16384)
    timeout: int = Field(default=30, ge=1, le=60)

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: str) -> str:
        """保留脚本原文，仅拒绝空命令和系统无法执行的 NUL。"""
        if not value.strip() or "\x00" in value:
            raise ValueError("请输入不含 NUL 的有效命令")
        return value


def shell_args(command: ShellCommand) -> list[str]:
    """POSIX 使用 /bin/sh；Windows 使用系统 cmd，不创建交互终端。"""
    if os.name == "nt":
        shell = os.path.join(
            os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32", "cmd.exe"
        )
        return [shell, "/d", "/s", "/c", command.command]
    return ["/bin/sh", "-c", command.command]
