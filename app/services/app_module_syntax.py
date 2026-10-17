"""只解析模块语法与字符串范围，不求值、不导入上传的 JavaScript。"""

import json
import re
from collections.abc import Iterator

import tree_sitter_javascript
from tree_sitter import Language, Node, Parser

from app.services.app_content_service import AppDomainError

LANGUAGE = Language(tree_sitter_javascript.language())


def module_nodes(content: bytes) -> Iterator[Node]:
    """固定语法版本、有界解析；不以正则猜测字符串/注释中的 import。"""
    content.decode("utf-8")
    parser = Parser(LANGUAGE)
    parser.timeout_micros = 1_000_000
    try:
        tree = parser.parse(content)
    except ValueError:
        raise AppDomainError("App 模块语法解析超时") from None
    if tree.root_node.has_error:
        raise AppDomainError("App 模块存在语法错误，请上传已构建 JavaScript")
    pending = [tree.root_node]
    while pending:
        node = pending.pop()
        if node.type.startswith("jsx_"):
            raise AppDomainError("App JSX 需要在作者本地构建后上传")
        yield node
        pending.extend(reversed(node.named_children))


def module_string(node: Node) -> str:
    """解码 JS 模块字符串常见转义，不把文字传给 Python/JS eval。"""
    raw = node.text.decode("utf-8")
    if node.type != "string":
        raise AppDomainError("App 模块引用必须为字符串")
    body = raw[1:-1]

    def escaped(match: re.Match) -> str:
        value = match.group()[1:]
        if value.startswith("u{"):
            return chr(int(value[2:-1], 16))
        if value.startswith(("u", "x")):
            return chr(int(value[1:], 16))
        return {
            "n": "\n",
            "r": "\r",
            "t": "\t",
            "b": "\b",
            "f": "\f",
            "v": "\v",
            "0": "\0",
            "\n": "",
            "\r\n": "",
        }.get(value, value)

    try:
        result = re.sub(
            r"\\(?:u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\r\n|[\s\S])",
            escaped,
            body,
        )
        result.encode("utf-8")
        return result
    except (ValueError, UnicodeError):
        raise AppDomainError("App 模块字符串转义不受支持") from None


def js_literal(value: str) -> bytes:
    """生成确定的 JS/JSON 字符串，HTML 边界不进入代码。"""
    return json.dumps(value, ensure_ascii=True).encode("ascii")


def import_meta_url(node: Node) -> bool:
    """结构化识别 import.meta.url，空白和注释不影响匹配。"""
    obj = node.child_by_field_name("object")
    prop = node.child_by_field_name("property")
    return (
        node.type == "member_expression"
        and obj is not None
        and obj.type == "meta_property"
        and prop is not None
        and prop.text == b"url"
    )


def json_import_attribute(node: Node) -> Node | None:
    """仅移除已转为 JS 包装模块的明确 JSON 类型声明，不忽略未知属性。"""
    attribute = next(
        (child for child in node.named_children if child.type == "import_attribute"),
        None,
    )
    if attribute is None:
        return None
    objects = [child for child in attribute.named_children if child.type == "object"]
    pairs = (
        [child for child in objects[0].named_children if child.type != "comment"]
        if len(objects) == 1
        else []
    )
    if len(pairs) != 1 or pairs[0].type != "pair":
        raise AppDomainError("App JSON 模块仅支持 type: json 导入属性")
    key = pairs[0].child_by_field_name("key")
    value = pairs[0].child_by_field_name("value")
    name = module_string(key) if key.type == "string" else key.text.decode()
    if name != "type" or value.type != "string" or module_string(value) != "json":
        raise AppDomainError("App JSON 模块仅支持 type: json 导入属性")
    return attribute
