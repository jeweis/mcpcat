"""测试客户端的显式能力配置；只声明资源，不访问宿主文件系统。"""

from typing import Annotated

from mcp.types import Root
from pydantic import BaseModel, ConfigDict, Field, FileUrl, UrlConstraints


class InspectorRoot(BaseModel):
    """用户手动声明的模拟客户端目录，不代表网关授予文件访问权。"""

    model_config = ConfigDict(extra="forbid")
    uri: Annotated[FileUrl, UrlConstraints(max_length=4096)]
    name: str | None = Field(default=None, max_length=256)


class InspectorClientOptions(BaseModel):
    """None 不声明 Roots；空数组明确声明支持但没有可用目录。"""

    model_config = ConfigDict(extra="forbid")
    roots: list[InspectorRoot] | None = Field(default=None, max_length=32)
    sampling: bool = False

    def client_kwargs(self) -> dict:
        """构建会话独立 SDK 模型，不读取或校验目录是否实际存在。"""
        if self.roots is None:
            return {}
        return {
            "roots": [Root(uri=root.uri, name=root.name) for root in self.roots]
        }
