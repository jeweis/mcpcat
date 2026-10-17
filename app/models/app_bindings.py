"""明确版本的 Apps 工具别名，不接受整目录或通配绑定。"""

from pydantic import BaseModel, ConfigDict, Field


class AppToolBinding(BaseModel):
    """别名只在当前 App 版本内有效，来源与工具名分开保存。"""

    model_config = ConfigDict(extra="forbid")
    alias: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
    server_name: str = Field(min_length=1, max_length=64)
    tool_name: str = Field(min_length=1, max_length=255)


class AppBindingsRequest(BaseModel):
    """完整替换草稿绑定，空数组明确撤回所有授权。"""

    model_config = ConfigDict(extra="forbid")
    bindings: list[AppToolBinding] = Field(default_factory=list, max_length=100)
