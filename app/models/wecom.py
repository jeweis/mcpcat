"""单企业自建应用的企业微信登录配置。"""

from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from app.models.mcp_config import PermissionType

CALLBACK_PATH = "/api/auth/wecom/callback"
FRONTEND_CALLBACK_PATH = "/login/wecom/callback"


class WeComConfig(BaseModel):
    """应用 Secret 仅在后端解密，前端响应使用独立模型。"""

    enabled: bool = False
    corp_id: str = Field(default="", max_length=255)
    agent_id: str = Field(default="", max_length=32)
    app_secret: str = ""
    redirect_uri: str = ""
    default_permission: PermissionType = PermissionType.READ

    @field_validator("corp_id", "agent_id", "app_secret", "redirect_uri")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """去除配置首尾空白。"""
        return value.strip()

    @field_validator("agent_id")
    @classmethod
    def validate_agent(cls, value: str) -> str:
        """非空 AgentID 必须是正整数。"""
        if value and (not value.isascii() or not value.isdigit() or int(value) <= 0):
            raise ValueError("AgentID 必须是正整数")
        return value

    @field_validator("redirect_uri")
    @classmethod
    def validate_redirect(cls, value: str) -> str:
        """限定固定后端回调路径，生产环境要求 HTTPS。"""
        if not value:
            return value
        parsed = urlsplit(value)
        # 触发非法端口校验；urlsplit 默认还会忽略部分控制字符。
        _ = parsed.port
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or any(character.isspace() for character in value)
            or not parsed.path.endswith(CALLBACK_PATH)
            or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
        ):
            raise ValueError("回调地址必须为 HTTPS 且以 /api/auth/wecom/callback 结尾")
        return value

    @property
    def frontend_callback(self) -> str:
        """从受信任的固定回调地址定位同源 Flutter UI。"""
        base = self.redirect_uri[: -len(CALLBACK_PATH)]
        return f"{base}/ui/#{FRONTEND_CALLBACK_PATH}"
