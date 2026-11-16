"""管理员自由命令与网络快捷项的参数构造，使用最小运行环境。"""

import os
import re
import shutil
import sys
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator

from app.services.shell_commands import ShellCommand, shell_args


class NetworkCommand(BaseModel):
    """显式参数构造命令，禁止通过目标注入选项或 shell 片段。"""

    kind: Literal["ping", "dns", "tcp", "http"]
    target: str = Field(min_length=1, max_length=2048)
    port: int = Field(default=443, ge=1, le=65535)
    count: int = Field(default=4, ge=1, le=10)
    timeout: int = Field(default=15, ge=1, le=60)

    @model_validator(mode="after")
    def validate_target(self) -> "NetworkCommand":
        """HTTP 仅接受无认证信息的 HTTP(S) URL；其他命令只接受主机。"""
        self.target = self.target.strip()
        if self.kind == "http":
            try:
                url = urlsplit(self.target)
                valid = (
                    url.scheme in {"http", "https"}
                    and url.hostname
                    and not url.username
                    and not url.password
                    and not url.fragment
                )
                _ = url.port
            except ValueError:
                valid = False
            if not valid or any(ord(char) < 33 for char in self.target):
                raise ValueError(
                    "请输入完整的 HTTP(S) URL，不包含账号、密码或 fragment"
                )
        elif not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:%_-]{0,252}", self.target):
            raise ValueError("请输入域名或 IP 地址，不包含命令参数")
        return self


def command_args(command: NetworkCommand | ShellCommand) -> list[str]:
    """自由命令交给系统 Shell；网络快捷项继续构造固定 argv。"""
    if isinstance(command, ShellCommand):
        return shell_args(command)
    if command.kind == "ping":
        executable = shutil.which("ping")
        if not executable:
            raise FileNotFoundError(
                "运行环境未安装 ping，请安装 iputils-ping 或使用 TCP 检查"
            )
        if sys.platform == "darwin" and ":" in command.target:
            executable = shutil.which("ping6") or executable
        count_flag = "-n" if os.name == "nt" else "-c"
        return [executable, count_flag, str(command.count), command.target]
    return [
        sys.executable,
        "-u",
        str(Path(__file__).with_name("network_probe.py")),
        command.kind,
        command.target,
        str(command.port),
        str(command.timeout),
    ]


def command_environment() -> dict[str, str]:
    """保留必要的系统运行环境，不传入 API Key、数据库密码等应用变量。"""
    allowed = {
        "PATH",
        "HOME",
        "USERPROFILE",
        "TEMP",
        "TMP",
        "TMPDIR",
        "SYSTEMROOT",
        "WINDIR",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}
