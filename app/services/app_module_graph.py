"""将包内模块映射到 data URL，保留模块身份/循环依赖，不执行上传代码。"""

import base64
import hashlib
import json
import posixpath
from urllib.parse import quote, urlsplit

from tree_sitter import Node

from app.services.app_asset_resolver import AppAssetResolver
from app.services.app_content_service import AppDomainError
from app.services.app_module_syntax import (
    import_meta_url,
    js_literal,
    json_import_attribute,
    module_nodes,
    module_string,
)


def reject_json_constant(value: str) -> None:
    """只接受标准 JSON 数值，不在客户运行期接受 NaN/Infinity。"""
    raise AppDomainError("App JSON 模块不允许非有限数值")


class AppModuleGraph:
    """同一包共享 import map；只改 AST 确认的路径表达式。"""

    def __init__(self, resolver: AppAssetResolver) -> None:
        self.resolver = resolver
        digest = hashlib.sha256()
        for path, content in sorted(resolver.files.items()):
            digest.update(path.encode() + b"\0" + hashlib.sha256(content).digest())
        self.prefix = "mcpcat-app://" + digest.hexdigest() + "/"
        self.pending: dict[str, tuple[str, bytes]] = {}
        self.imports: dict[str, str] = {}

    def uri(self, path: str) -> str:
        """私有虚拟标识不对应网关 HTTP 地址，也不携带凭证。"""
        return self.prefix + quote(path, safe="/")

    def reference(self, value: str, base: str) -> str:
        """静态模块引用可使用包内相对路径或已审核外部 HTTPS。"""
        if not value.startswith((".", "/")) and not urlsplit(value).scheme:
            # bare specifier 留给作者已有的 import map，不安装 npm 依赖。
            return value
        path = self.resolver.local_path(value, base)
        if path is None:
            return value
        if urlsplit(value).query or urlsplit(value).fragment:
            raise AppDomainError(
                "包内模块查询参数/fragment 需在作者本地预构建为明确文件"
            )
        return self.add(path, self.resolver.files[path])

    def add(self, path: str, content: bytes) -> str:
        """先登记再装配，循环依赖不会递归展开或重复复制模块。"""
        uri = self.uri(path)
        if uri not in self.imports:
            self.pending.setdefault(uri, (path, content))
        return uri

    def _rewrite(self, path: str, content: bytes) -> bytes:
        replacements: list[tuple[int, int, bytes]] = []
        for node in module_nodes(content):
            source = node.child_by_field_name("source")
            if node.type in {"import_statement", "export_statement"} and source:
                value = self.reference(module_string(source), path)
                replacements.append(
                    (source.start_byte, source.end_byte, js_literal(value))
                )
                if value.startswith(self.prefix) and urlsplit(value).path.endswith(
                    ".json"
                ):
                    attribute = json_import_attribute(node)
                    if attribute is not None:
                        replacements.append(
                            (attribute.start_byte, attribute.end_byte, b"")
                        )
            if node.type == "call_expression":
                self._dynamic_import(node, path, replacements)
            if node.type == "new_expression":
                self._asset_url(node, path, replacements)
            if import_meta_url(node):
                replacements.append(
                    (node.start_byte, node.end_byte, js_literal(self.uri(path)))
                )
        for start, end, value in sorted(replacements, reverse=True):
            content = content[:start] + value + content[end:]
        return content

    def _dynamic_import(self, node: Node, path: str, replacements: list) -> None:
        function = node.child_by_field_name("function")
        if function is None or function.type != "import":
            return
        arguments = [
            child
            for child in node.child_by_field_name("arguments").named_children
            if child.type != "comment"
        ]
        if not arguments or arguments[0].type != "string":
            raise AppDomainError(
                "App 动态 import 需使用明确字符串路径，请在作者本地预构建"
            )
        source = arguments[0]
        value = self.reference(module_string(source), path)
        replacements.append((source.start_byte, source.end_byte, js_literal(value)))

    def _asset_url(self, node: Node, path: str, replacements: list) -> None:
        constructor = node.child_by_field_name("constructor")
        arguments = node.child_by_field_name("arguments")
        if constructor is None or constructor.text != b"URL" or arguments is None:
            return
        values = arguments.named_children
        if len(values) != 2 or not import_meta_url(values[1]):
            return
        if values[0].type != "string":
            raise AppDomainError("App import.meta.url 资产需使用明确字符串路径")
        value = self.resolver.data_uri(module_string(values[0]), path)
        replacements.append(
            (values[0].start_byte, values[0].end_byte, js_literal(value))
        )

    def render(self) -> str:
        """所有模块只经过语法解析，最终 JS 仅由客户 sandbox 浏览器执行。"""
        size = 0
        while self.pending:
            uri, (path, content) = self.pending.popitem()
            if uri in self.imports:
                continue
            self.imports[uri] = ""  # 在处理循环引用前保留此模块身份。
            if posixpath.splitext(path)[1].lower() == ".json":
                # 浏览器对 JSON import attributes 支持不一；固定包装只在客户
                # 浏览器 JSON.parse，避免服务端执行或把 __proto__ 当对象语法。
                text = content.decode("utf-8")
                json.loads(text, parse_constant=reject_json_constant)
                data, media = (
                    b"export default JSON.parse(" + js_literal(text) + b");",
                    "text/javascript",
                )
            else:
                data, media = self._rewrite(path, content), "text/javascript"
            # data URL 的内容也是模块缓存身份；相同源码的不同路径不能合并。
            data += (
                b"\n// mcpcat-module-identity:"
                + hashlib.sha256(uri.encode()).hexdigest().encode()
            )
            value = "data:" + media + ";base64," + base64.b64encode(data).decode()
            size += len(value)
            if size > 64 * 1024 * 1024 or len(self.imports) > 1024:
                raise AppDomainError("App 模块装配超过容量限制")
            self.imports[uri] = value
        if not self.imports:
            return ""
        payload = json.dumps(
            {"imports": self.imports}, sort_keys=True, ensure_ascii=True
        ).replace("<", "\\u003c")
        return '<script type="importmap">' + payload + "</script>"
