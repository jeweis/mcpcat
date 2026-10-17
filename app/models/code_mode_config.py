"""固定提供的编排能力；不允许无界或匿名执行。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.platform_endpoint_config import PlatformEndpointConfig


class CodeModeLimits(BaseModel):
    """Monty 计算限制与调用桥接限制独立，均为有限正数。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    sandbox_seconds: float = Field(default=30.0, gt=0, le=300)
    memory_bytes: int = Field(default=100_000_000, gt=0, le=1_000_000_000)
    max_tool_calls: int = Field(default=50, gt=0, le=1000)
    tool_concurrency: int = Field(default=4, gt=0, le=32)
    principal_concurrency: int = Field(default=2, gt=0, le=16)
    tool_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    deadline_seconds: float = Field(default=120.0, gt=0, le=600)
    max_code_bytes: int = Field(default=65_536, gt=0, le=1_048_576)

    def monty_limits(self) -> dict[str, float | int]:
        """只传固定公开 API 支持的计算参数，等待限制由桥接层控制。"""
        return {
            "max_feed_duration_secs": self.sandbox_seconds,
            "max_memory": self.memory_bytes,
        }


class CodeModeConfig(PlatformEndpointConfig):
    """None 不额外缩小 Catalog 范围，空列表明确表示不允许任何项。"""

    model_config = ConfigDict(extra="allow", strict=True)
    path_name: str = Field(default="code-mode", pattern=r"^[a-z][a-z0-9-]{0,63}$")
    require_auth: Literal[True] = True
    allowed_services: list[str] | None = Field(default=None, max_length=128)
    allowed_tools: list[str] | None = Field(default=None, max_length=1024)
    limits: CodeModeLimits = Field(default_factory=CodeModeLimits)

    @field_validator("allowed_services", "allowed_tools")
    @classmethod
    def validate_scope(cls, value: list[str] | None) -> list[str] | None:
        """明确名称列表，不解释通配符或将空列表误当无限范围。"""
        if value is not None and any(
            not item or item != item.strip() or len(item) > 512 or "\x00" in item
            for item in value
        ):
            raise ValueError("允许范围必须使用非空、无首尾空格的明确公开名称")
        return list(dict.fromkeys(value)) if value is not None else None
