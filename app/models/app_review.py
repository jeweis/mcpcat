"""上传 Apps 的审核配置，使用官方字段但禁止未知权限扩张。"""

import ipaddress
import re
from urllib.parse import urlsplit

from fastmcp.apps import ResourceCSP, ResourcePermissions
from pydantic import ConfigDict, field_validator


class AppCSP(ResourceCSP):
    """外部来源需明确审核，仅接收有界 HTTPS origin。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @field_validator(
        "connect_domains", "resource_domains", "frame_domains", "base_uri_domains"
    )
    @classmethod
    def validate_origins(cls, values: list[str] | None) -> list[str] | None:
        """不接受通配符、凭证、路径或把 CSP 指令塞入 origin。"""
        if values is None:
            return None
        if len(values) > 32 or len(values) != len(set(values)):
            raise ValueError("App 域名数量过多或重复")
        for value in values:
            parsed = urlsplit(value)
            if (
                len(value) > 255
                or any(char.isspace() for char in value)
                or any(char in value for char in "*;\\\"'<>&%?#")
                or any(ord(char) < 32 or ord(char) > 126 for char in value)
                or parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("App 域名必须是明确的 HTTPS origin")
            try:
                port = parsed.port
                host = parsed.hostname
                if ":" in host:
                    ipaddress.IPv6Address(host)
                elif not all(
                    re.fullmatch(
                        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label
                    )
                    for label in host.split(".")
                ):
                    raise ValueError("invalid hostname")
                if parsed.netloc.endswith(":") or port == 0:
                    raise ValueError("invalid port")
            except ValueError as error:
                raise ValueError(
                    "App 域名或端口无效，请使用 ASCII/punycode origin"
                ) from error
        return values


class AppPermissions(ResourcePermissions):
    """只允许官方已知权限，不允许注入任意 sandbox 属性。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @field_validator("camera", "microphone", "geolocation", "clipboard_write")
    @classmethod
    def validate_permission(cls, value: dict | None) -> dict | None:
        """权限请求只保留官方空对象形式，Host 仍需用户批准。"""
        if value:
            raise ValueError("App 权限请求仅支持空对象")
        return value
