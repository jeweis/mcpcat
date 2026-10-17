"""v5 模块语法适配，保留 v4 已冻结内容和字节级改写规则。"""

import hashlib
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from tree_sitter import Node

from app.services.app_content_service import AppDomainError
from app.services.app_module_syntax import (
    import_meta_url,
    js_literal,
    json_import_attribute,
    module_nodes,
    module_string,
)

if TYPE_CHECKING:
    from app.services.app_module_graph_v5 import AppModuleGraphV5

META_URL = (
    b'decodeURIComponent(import.meta.url.slice(import.meta.url.lastIndexOf("#")+1))'
)


def named_arguments(node: Node) -> list[Node]:
    """注释不是参数，不能改变动态 import 或 URL 的参数计数。"""
    arguments = node.child_by_field_name("arguments")
    return (
        [child for child in arguments.named_children if child.type != "comment"]
        if arguments
        else []
    )


def import_edit(graph: "AppModuleGraphV5", node: Node, uri: str, edits: list) -> None:
    """每个动态表达式只求值一次，嵌套语法保留原始参数。"""
    function = node.child_by_field_name("function")
    if function is None or function.type != "import":
        return
    arguments = named_arguments(node)
    if not arguments or len(arguments) > 2:
        raise AppDomainError("App 动态 import 参数不合法")
    if arguments[0].type == "string":
        graph.reference(module_string(arguments[0]), uri)
    graph.require_runtime("import")
    alias = graph.aliases["import"].encode()
    edits.append(
        (
            function.start_byte,
            function.end_byte,
            alias + b".bind(null," + META_URL + b")",
        )
    )


def asset_edit(graph: "AppModuleGraphV5", node: Node, uri: str, edits: list) -> None:
    """包内静态 URL 直接装配，计算型 URL 由固定客户装载器查表。"""
    constructor = node.child_by_field_name("constructor")
    values = named_arguments(node)
    if (
        constructor is None
        or constructor.type != "identifier"
        or constructor.text != b"URL"
        or len(values) != 2
        or not import_meta_url(values[1])
    ):
        return
    if values[0].type == "string":
        reference = module_string(values[0])
        base = graph.path_for(uri)
        value = graph.resolver.data_uri(reference, base)
        edits.append((values[0].start_byte, values[0].end_byte, js_literal(value)))
    else:
        graph.require_runtime("asset")
        prefix = graph.aliases["asset"].encode() + b".bind(null," + META_URL + b")("
        edits.extend(
            [
                (values[0].start_byte, values[0].start_byte, prefix),
                (values[0].end_byte, values[0].end_byte, b")"),
            ]
        )


def rewrite_module(graph: "AppModuleGraphV5", uri: str, content: bytes) -> bytes:
    """只改 AST 确认的引用、import 函数及 import.meta.url，不运行模块。"""
    edits: list[tuple[int, int, bytes]] = []
    graph.aliases = {
        kind: "__mcpcat_" + hashlib.sha256((uri + kind).encode()).hexdigest()
        for kind in ("import", "asset")
    }
    for alias in graph.aliases.values():
        if alias.encode() in content:
            raise AppDomainError("App 模块与保留的装配符号冲突")
    graph.used_runtime = set()
    for node in module_nodes(content):
        source = node.child_by_field_name("source")
        if node.type in {"import_statement", "export_statement"} and source is not None:
            target = graph.reference(module_string(source), uri)
            edits.append((source.start_byte, source.end_byte, js_literal(target)))
            if target.startswith(graph.prefix) and urlsplit(
                target
            ).path.lower().endswith(".json"):
                attribute = json_import_attribute(node)
                if attribute is not None:
                    edits.append((attribute.start_byte, attribute.end_byte, b""))
        if node.type == "call_expression":
            import_edit(graph, node, uri, edits)
        if node.type == "new_expression":
            asset_edit(graph, node, uri, edits)
        if import_meta_url(node):
            edits.append((node.start_byte, node.end_byte, META_URL))
    for start, end, value in sorted(edits, reverse=True):
        content = content[:start] + value + content[end:]
    if graph.used_runtime:
        exports = {"import": "importModule", "asset": "assetURL"}
        bindings = ",".join(
            exports[kind] + " as " + graph.aliases[kind]
            for kind in sorted(graph.used_runtime)
        )
        prefix = (
            b"import {"
            + bindings.encode()
            + b"} from "
            + js_literal(graph.runtime_uri)
            + b";\n"
        )
        offset = content.find(b"\n") + 1 if content.startswith(b"#!") else 0
        content = content[:offset] + prefix + content[offset:]
    return content
