"""用 tinycss2 语法树装配 CSS 附件，不用正则替换代码。"""

import json

import tinycss2

from app.services.app_asset_resolver import AppAssetResolver
from app.services.app_content_service import AppDomainError


def _url(value: str) -> list:
    return tinycss2.parse_component_value_list(
        "url(" + json.dumps(value, ensure_ascii=False) + ")"
    )


def assemble_app_css_value(css: str, *, resolver: AppAssetResolver, base: str) -> str:
    """style 属性的 component values，不把声明误当完整 stylesheet。"""
    return tinycss2.serialize(
        _tokens(tinycss2.parse_component_value_list(css), resolver, base)
    )


def _tokens(
    tokens: list, resolver: AppAssetResolver, base: str, depth: int = 0
) -> list:
    if depth > 64:
        raise AppDomainError("App CSS 结构超过限制")
    result = []
    for token in tokens:
        if token.type == "error":
            raise AppDomainError("App CSS 语法无效")
        if token.type == "url":
            result.extend(_url(resolver.data_uri(token.value, base)))
        elif token.type == "function" and token.lower_name == "url":
            values = [
                item
                for item in token.arguments
                if item.type not in {"comment", "whitespace"}
            ]
            if len(values) != 1 or values[0].type != "string":
                raise AppDomainError("App CSS url() 引用无效")
            result.extend(_url(resolver.data_uri(values[0].value, base)))
        else:
            if hasattr(token, "content"):
                token.content = _tokens(token.content, resolver, base, depth + 1)
            if hasattr(token, "arguments"):
                token.arguments = _tokens(token.arguments, resolver, base, depth + 1)
            result.append(token)
    return result


def _import(rule, resolver: AppAssetResolver, base: str, stack: tuple[str, ...]) -> str:
    tokens = [
        item for item in rule.prelude if item.type not in {"comment", "whitespace"}
    ]
    if not tokens:
        raise AppDomainError("App CSS import 缺少目标")
    first = tokens[0]
    if first.type in {"string", "url"}:
        reference = first.value
    elif first.type == "function" and first.lower_name == "url":
        values = [
            item
            for item in first.arguments
            if item.type not in {"comment", "whitespace"}
        ]
        if len(values) != 1 or values[0].type != "string":
            raise AppDomainError("App CSS import 目标无效")
        reference = values[0].value
    else:
        raise AppDomainError("App CSS import 目标无效")
    path = resolver.local_path(reference, base)
    if path is None:
        return rule.serialize()
    try:
        css = resolver.files[path].decode("utf-8")
    except UnicodeDecodeError:
        raise AppDomainError("App CSS 必须为 UTF-8") from None
    imported = assemble_app_css(css, resolver=resolver, base=path, stack=stack)
    return _import_conditions(rule.prelude[rule.prelude.index(first) + 1 :], imported)


def _trim(tokens: list) -> list:
    while tokens and tokens[0].type in {"comment", "whitespace"}:
        tokens = tokens[1:]
    return tokens


def _import_conditions(tokens: list, content: str) -> str:
    """按规范让条件包围 layer，条件为假时不激活该层。"""
    tokens = _trim(tokens)
    layer, tokens = _layer(tokens)
    supports = None
    if tokens and tokens[0].type == "function" and tokens[0].lower_name == "supports":
        supports = tinycss2.serialize(tokens[0].arguments).strip()
        if not supports:
            raise AppDomainError("App CSS import supports 条件不能为空")
        tokens = _trim(tokens[1:])
    if any(
        item.type == "function"
        and item.lower_name in {"layer", "supports"}
        or item.type == "ident"
        and item.lower_value == "layer"
        for item in tokens
    ):
        raise AppDomainError("App CSS import 条件顺序或数量无效")
    media = tinycss2.serialize(tokens).strip()
    if layer is not None:
        content = f"@layer {layer}{{{content}}}"
    if media:
        content = f"@media {media}{{{content}}}"
    if supports is not None:
        content = f"@supports ({supports}){{{content}}}"
    return content


def _layer(tokens: list) -> tuple[str | None, list]:
    layer = None
    if tokens and tokens[0].type == "ident" and tokens[0].lower_value == "layer":
        layer, tokens = "", _trim(tokens[1:])
    elif tokens and tokens[0].type == "function" and tokens[0].lower_name == "layer":
        arguments = [
            item
            for item in tokens[0].arguments
            if item.type not in {"comment", "whitespace"}
        ]
        if (
            not arguments
            or any(
                (
                    item.type != "ident"
                    if index % 2 == 0
                    else item.type != "literal" or item.value != "."
                )
                for index, item in enumerate(arguments)
            )
            or len(arguments) % 2 == 0
        ):
            raise AppDomainError("App CSS import layer 名称无效")
        layer = tinycss2.serialize(tokens[0].arguments).strip()
        tokens = _trim(tokens[1:])
    return layer, tokens


def assemble_app_css(
    css: str, *, resolver: AppAssetResolver, base: str, stack: tuple[str, ...] = ()
) -> str:
    """重写 URL 节点并展开包内 import，原始附件不变。"""
    if base in stack or len(stack) >= 32:
        raise AppDomainError("App CSS import 循环或层级超过限制")
    stack = (*stack, base)
    output = []
    size = 0
    imports_allowed = True
    for rule in tinycss2.parse_stylesheet(
        css, skip_comments=False, skip_whitespace=False
    ):
        if rule.type == "error":
            raise AppDomainError("App CSS 语法无效")
        if rule.type == "at-rule" and rule.lower_at_keyword == "import":
            if not imports_allowed:
                raise AppDomainError("App CSS import 必须位于其他规则之前")
            value = _import(rule, resolver, base, stack)
        else:
            if (
                rule.type == "qualified-rule"
                or rule.type == "at-rule"
                and not (
                    rule.lower_at_keyword == "charset"
                    or rule.lower_at_keyword == "layer"
                    and rule.content is None
                )
            ):
                imports_allowed = False
            value = _render_rule(rule, resolver, base)
        size += len(value.encode())
        if size > 64 * 1024 * 1024:
            raise AppDomainError("App 派生 CSS 超过容量限制")
        output.append(value)
    return "".join(output)


def _render_rule(rule, resolver: AppAssetResolver, base: str) -> str:
    if rule.type == "at-rule" and rule.lower_at_keyword == "charset":
        return ""
    if hasattr(rule, "content") and rule.content is not None:
        rule.content = _tokens(rule.content, resolver, base)
    if hasattr(rule, "prelude"):
        rule.prelude = _tokens(rule.prelude, resolver, base)
    return rule.serialize()
