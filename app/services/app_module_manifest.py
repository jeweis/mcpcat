"""模块清单输出预算：别名/前缀不能在 JSON 序列化前无限放大。"""

import base64
import json
import mimetypes
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from app.services.app_content_service import AppDomainError

if TYPE_CHECKING:
    from app.services.app_module_graph_v5 import AppModuleGraphV5

LIMIT = 64 * 1024 * 1024


def data_url(content: bytes, media: str) -> str:
    """只编码已读取的字节，不根据 URL 读取网络或宿主文件。"""
    return f"data:{media};base64," + base64.b64encode(content).decode("ascii")


def encoded_manifest(value: dict) -> str:
    """JSON 文本不突破 HTML script 边界。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=True).replace("<", "\\u003c")


def wire_length(value: str | None) -> int:
    """已生成 data URL 是安全 ASCII，不重复复制大值来计算容量。"""
    if value is None:
        return 4
    if (
        value.startswith("data:")
        and value.isascii()
        and '"' not in value
        and "\\" not in value
    ):
        return len(value) + 2
    return len(json.dumps(value, ensure_ascii=True))


def mapping_length(mapping: dict) -> int:
    """包含 JSON 字符串转义、冒号和逗号的保守容量。"""
    return 2 + sum(
        wire_length(key) + wire_length(value) + 2 for key, value in mapping.items()
    )


class ManifestBudget:
    """同时限制派生映射数量和别名重复引用后的实际序列化大小。"""

    def __init__(self, size: int) -> None:
        self.size, self.entries = size, 0
        self.check()

    def check(self) -> None:
        """在分配/拼接下一批清单前拒绝，不能等大 JSON 生成才检查。"""
        if self.size > LIMIT or self.entries > 4096:
            raise AppDomainError("App 模块清单展开超过容量/映射数量限制")

    def add(self, key: str, value: str | None) -> None:
        """每一条展开别名都占用预算，不因引用同一字符串而省略计数。"""
        self.entries += 1
        self.size += wire_length(key) + wire_length(value) + 2
        self.check()


def native_mapping(
    graph: "AppModuleGraphV5", mapping: dict, budget: ManifestBudget
) -> dict:
    """展开包内前缀为原生可用的单跳 data URL，外部映射保留原地址。"""
    result = {}
    for key, target in mapping.items():
        if target is None or not target.startswith(graph.prefix):
            values = [(key, target)]
        elif key.endswith("/"):
            values = (
                (key + uri[len(target) :], value)
                for uri, value in graph.imports.items()
                if uri.startswith(target)
            )
        else:
            values = [(key, graph.imports.get(target))]
        for name, value in values:
            budget.add(name, value)
            result[name] = value
    return result


def render_manifest(graph: "AppModuleGraphV5") -> str:
    """固定配置只有包内公开字节/审核域名，没有网关凭证或宿主路径。"""
    assets = {}
    if graph.assets_required:
        for path, content in graph.resolver.files.items():
            media = mimetypes.guess_type(path)[0] or "application/octet-stream"
            assets[graph.uri(path)] = data_url(content, media)
    budget = ManifestBudget(
        2 * mapping_length(graph.imports)
        + mapping_length(assets)
        + len(encoded_manifest(graph.maps.manifest()))
        + 4096
    )
    native = {**native_mapping(graph, graph.maps.imports, budget), **graph.imports}
    scopes = {
        scope: native_mapping(graph, mapping, budget)
        for scope, mapping in graph.maps.scopes.items()
    }
    config = {
        "prefix": graph.prefix,
        "maps": graph.maps.manifest(),
        "modules": graph.imports,
        "assets": assets,
        "resourceDomains": sorted(graph.resolver.resource_domains),
    }
    identity = urlsplit(graph.prefix).hostname
    return (
        '<script type="importmap">'
        + encoded_manifest({"imports": native, "scopes": scopes})
        + '</script><script type="application/json" data-mcpcat-module-config="'
        + identity
        + '">'
        + encoded_manifest(config)
        + "</script>"
    )
