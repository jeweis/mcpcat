"""Apps 固定提供管理与分发，资源公开仍需独立审核。"""

from pydantic import ConfigDict, Field

from app.models.platform_endpoint_config import PlatformEndpointConfig


class AppsMCPConfig(PlatformEndpointConfig):
    """保留未知根配置，不接受含斜线或特殊符号的路径。"""

    model_config = ConfigDict(extra="allow")
    path_name: str = Field(default="apps", pattern=r"^[a-z][a-z0-9-]{0,63}$")
    require_auth: bool = True
