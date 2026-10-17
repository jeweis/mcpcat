"""远程 Skills 配置，与旧 Skills REST/CLI 开关分离。"""

from pydantic import ConfigDict, Field

from app.models.platform_endpoint_config import PlatformEndpointConfig


class SkillsMCPConfig(PlatformEndpointConfig):
    """缺失配置按安全默认解释，不改写旧实例的配置原文。"""

    model_config = ConfigDict(extra="allow")
    path_name: str = Field(default="skills", pattern=r"^[a-z][a-z0-9-]{0,63}$")
    require_auth: bool = True
    page_size: int = Field(default=25, ge=1, le=100)
