"""作者 import map 的规范化/作用域解析，只读取已审核包的路径。"""

import json
import posixpath
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from app.services.app_asset_resolver import AppAssetResolver
from app.services.app_content_service import AppDomainError
from app.services.app_package_validator import safe_app_path


def unique_json_object(pairs: list[tuple]) -> dict:
    """拒绝重复键，不让前后端选择不同的映射。"""
    result = {}
    for key, value in pairs:
        if key in result:
            raise AppDomainError("App import map 包含重复键")
        result[key] = value
    return result


def validate_map_limits(imports: dict, scopes: dict) -> None:
    """总限制也覆盖多份作者 map 合并，不能用拆分标签绕过。"""
    entries = len(imports) + sum(
        len(value) if isinstance(value, dict) else 2049 for value in scopes.values()
    )
    if len(scopes) > 512 or entries > 2048:
        raise AppDomainError("App import map 超过映射数量限制")


class ModuleImportMaps:
    """先匹配最长 scope，再匹配精确键/最长前缀，null 明确阻断。"""

    def __init__(self, prefix: str, resolver: AppAssetResolver) -> None:
        self.prefix, self.resolver = prefix, resolver
        self.imports: dict[str, str | None] = {}
        self.scopes: dict[str, dict[str, str | None]] = {}

    def url(self, reference: str, base: str) -> str:
        """虚拟 URL 不经过真实宿主文件系统，包边界外路径明确拒绝。"""
        if (
            any(char.isspace() or ord(char) < 32 for char in reference)
            or "\\" in reference
        ):
            raise AppDomainError("App 模块路径包含非法字符")
        if reference.startswith(self.prefix):
            return reference
        parsed = urlsplit(reference)
        if parsed.scheme or parsed.netloc:
            self.resolver.local_path(reference, "index.html")
            return reference
        raw = unquote(parsed.path, errors="strict")
        parent = unquote(urlsplit(base).path.lstrip("/"), errors="strict")
        path = posixpath.normpath(
            raw.lstrip("/")
            if raw.startswith("/")
            else posixpath.join(posixpath.dirname(parent), raw)
        )
        if path == ".":
            path = ""
        elif path:
            safe_app_path(path)
        directory = "/" if raw.endswith("/") and path else ""
        return (
            self.prefix
            + quote(path, safe="/")
            + directory
            + urlunsplit(("", "", "", parsed.query, parsed.fragment))
        )

    def _key(self, value: str, base: str) -> str:
        return (
            self.url(value, base)
            if value.startswith((".", "/")) or urlsplit(value).scheme
            else value
        )

    def _entries(self, values: dict, base: str) -> dict[str, str | None]:
        if not isinstance(values, dict):
            raise AppDomainError("App import map 映射必须为对象")
        result = {}
        for key, value in values.items():
            if not key or value is not None and not isinstance(value, str):
                raise AppDomainError("App import map 地址必须为字符串或 null")
            normalized = self._key(key, base)
            target = None if value is None else self.url(value, base)
            if key.endswith("/") and target is not None and not target.endswith("/"):
                raise AppDomainError("App import map 前缀目标必须以 / 结束")
            if normalized in result:
                raise AppDomainError("App import map 规范化后键冲突")
            result[normalized] = target
        return result

    def add(self, content: str, entrypoint: str) -> None:
        """只接收 imports/scopes；既有映射优先，不静默覆盖早期映射。"""
        try:
            wire = json.loads(content, object_pairs_hook=unique_json_object)
        except (ValueError, TypeError):
            raise AppDomainError("App import map JSON 不合法") from None
        if not isinstance(wire, dict) or set(wire) - {"imports", "scopes"}:
            raise AppDomainError("App import map 仅支持 imports/scopes")
        scopes = wire.get("scopes", {})
        imports = wire.get("imports", {})
        if not isinstance(scopes, dict) or not isinstance(imports, dict):
            raise AppDomainError("App import map imports/scopes 必须为对象")
        validate_map_limits(imports, scopes)
        base = self.prefix + quote(entrypoint, safe="/")
        for key, value in self._entries(wire.get("imports", {}), base).items():
            self.imports.setdefault(key, value)
        for key, mapping in scopes.items():
            scope = self.url(key, base)
            current = self.scopes.setdefault(scope, {})
            for name, target in self._entries(mapping, base).items():
                current.setdefault(name, target)
        validate_map_limits(self.imports, self.scopes)

    def _match(
        self, value: str, mapping: dict[str, str | None]
    ) -> tuple[bool, str | None]:
        if value in mapping:
            return True, mapping[value]
        for key in sorted(mapping, key=len, reverse=True):
            if key.endswith("/") and value.startswith(key):
                target = mapping[key]
                return True, None if target is None else target + value[len(key) :]
        return False, None

    def resolve(self, value: str, base: str) -> str:
        """未映射的 bare import 不能伪装成功或触发服务器安装依赖。"""
        normalized = self._key(value, base)
        mappings = [
            mapping
            for scope, mapping in sorted(
                self.scopes.items(), key=lambda item: len(item[0]), reverse=True
            )
            if base.startswith(scope)
        ]
        for mapping in [*mappings, self.imports]:
            matched, target = self._match(normalized, mapping)
            if matched:
                if target is None:
                    raise AppDomainError("App import map 已阻止此模块")
                return target
        if normalized.startswith(self.prefix) or urlsplit(normalized).scheme:
            return normalized
        raise AppDomainError("App 模块缺少 bare specifier 的 import map")

    def manifest(self) -> dict:
        """客户端计算型 import 复用相同的公开作用域映射，不含凭证。"""
        return {"imports": self.imports, "scopes": self.scopes}
