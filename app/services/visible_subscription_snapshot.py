"""协议目录的可见快照；仅比较元数据，不调用工具或读取资源正文。"""

import hashlib
import json
from dataclasses import dataclass, field
from collections.abc import Iterable
from typing import Any


def protocol_fingerprint(model: Any) -> str:
    """使用稳定 JSON 摘要，避免序列化顺序产生虚假通知。"""
    value = json.dumps(model.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class VisibleSnapshot:
    """同一身份下的 URI/名称及内容或协议元数据摘要。"""

    resources: dict[str, str] = field(default_factory=dict)
    tools: dict[str, str] = field(default_factory=dict)
    prompts: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_components(
        cls, resources: Iterable[Any], tools: Iterable[Any]
    ) -> "VisibleSnapshot":
        """复用公开组件的 wire 模型，不包含执行回调或框架内部对象。"""
        return cls(
            resources={
                str(item.uri): protocol_fingerprint(item.to_mcp_resource())
                for item in resources
            },
            tools={
                item.name: protocol_fingerprint(item.to_mcp_tool()) for item in tools
            },
        )
