"""平台能力固定提供；旧客户端的功能开关仅作兼容输入。"""

from typing import Any

from pydantic import BaseModel, model_validator


class PlatformEndpointConfig(BaseModel):
    """剔除废弃开关，不把资源认证、范围或限制当作功能开关。"""

    @model_validator(mode="before")
    @classmethod
    def ignore_legacy_switches(cls, value: Any) -> Any:
        """接受旧配置与旧管理客户端，不修改调用者原字典。"""
        if isinstance(value, dict):
            return {
                key: item
                for key, item in value.items()
                if key not in {"enabled", "independent_enabled"}
            }
        return value
