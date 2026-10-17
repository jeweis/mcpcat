"""v5 模块图：作者映射、计算型导入和 URL 身份，v4 原样保留。"""

import json
import posixpath
from pathlib import Path
from time import monotonic
from urllib.parse import quote, unquote, urlsplit

from app.services.app_asset_resolver import AppAssetResolver
from app.services.app_content_service import AppDomainError
from app.services.app_module_graph import AppModuleGraph, reject_json_constant
from app.services.app_module_manifest import data_url, render_manifest
from app.services.app_module_maps import ModuleImportMaps
from app.services.app_module_rewrite_v5 import rewrite_module
from app.services.app_module_syntax import js_literal
from app.services.app_package_validator import safe_app_path


class AppModuleGraphV5(AppModuleGraph):
    """固定客户端装载器查公开清单；服务端仍只做有界语法装配。"""

    def __init__(self, resolver: AppAssetResolver) -> None:
        super().__init__(resolver)
        self.maps = ModuleImportMaps(self.prefix, resolver)
        self.runtime_uri = self.prefix + "?__mcpcat_runtime=v5"
        self.assets_required = False
        self.catalogued = False
        self.aliases: dict[str, str] = {}
        self.used_runtime: set[str] = set()
        self.source_paths: dict[str, str] = {}

    def path_for(self, uri: str) -> str:
        """只解析本包身份，查询和片段不属于真实文件路径。"""
        if not uri.startswith(self.prefix):
            raise AppDomainError("App 模块不属于当前包")
        if uri in self.source_paths:
            return self.source_paths[uri]
        path = unquote(urlsplit(uri).path.lstrip("/"), errors="strict")
        safe_app_path(path)
        return path

    def reference(self, value: str, base: str) -> str:
        """读取当前包/作用域映射；相同文件不同参数保留独立 URI。"""
        if not base.startswith(self.prefix):
            base = self.uri(base)
        target = self.maps.resolve(value, base)
        if not target.startswith(self.prefix):
            self.resolver.local_path(target, "index.html")
            return target
        path = self.path_for(target)
        if path not in self.resolver.files:
            raise AppDomainError("App 引用的包内模块不存在")
        return self.add(path, self.resolver.files[path], identifier=target)

    def add(self, path: str, content: bytes, *, identifier: str | None = None) -> str:
        """显式 URI 承载参数；本地文件清单不会被添加查询参数伪文件。"""
        uri = identifier or self.uri(path)
        self.source_paths.setdefault(uri, path)
        if uri not in self.imports:
            self.pending.setdefault(uri, (path, content))
        return uri

    def catalogue(self) -> None:
        """计算型导入只能取包内已构建模块，绝不运行 npm 安装/构建。"""
        if self.catalogued:
            return
        self.catalogued = True
        for path, content in self.resolver.files.items():
            if posixpath.splitext(path)[1].lower() in {".js", ".mjs", ".json"}:
                self.add(path, content)

    def require_runtime(self, kind: str) -> None:
        """固定装载器单例；按需装配模块或资产清单，不注入凭证。"""
        self.used_runtime.add(kind)
        self.pending.setdefault(self.runtime_uri, ("", b""))
        if kind == "import":
            self.catalogue()
        else:
            self.assets_required = True

    def configure_map(self, content: str, entrypoint: str) -> None:
        """作者映射先解析再装配，不依赖 browser 多 import map 合并差异。"""
        self.maps.add(content, entrypoint)
        self.catalogue()
        for mapping in [self.maps.imports, *self.maps.scopes.values()]:
            for target in mapping.values():
                if (
                    target
                    and target.startswith(self.prefix)
                    and not target.endswith("/")
                ):
                    path = self.path_for(target)
                    if path not in self.resolver.files:
                        raise AppDomainError("App import map 指向不存在的包内模块")
                    self.add(path, self.resolver.files[path], identifier=target)
        for scope in self.maps.scopes:
            if scope.startswith(self.prefix) and not scope.endswith("/"):
                path = unquote(urlsplit(scope).path.lstrip("/"), errors="strict")
                if path in self.resolver.files:
                    self.add(path, self.resolver.files[path], identifier=scope)

    def _content(self, uri: str, path: str, content: bytes) -> bytes:
        if uri == self.runtime_uri:
            return Path(__file__).with_name("app_module_runtime_v5.js").read_bytes()
        if posixpath.splitext(path)[1].lower() == ".json":
            text = content.decode("utf-8")
            json.loads(text, parse_constant=reject_json_constant)
            return b"export default JSON.parse(" + js_literal(text) + b");"
        return rewrite_module(self, uri, content)

    def render(self) -> str:
        """十秒整体装配预算；每模块解析另有固定一秒限制。"""
        size, deadline = 0, monotonic() + 10
        while self.pending:
            if monotonic() >= deadline:
                raise AppDomainError("App 模块整体装配超时")
            uri, (path, content) = self.pending.popitem()
            if uri in self.imports:
                continue
            self.imports[uri] = ""
            value = data_url(self._content(uri, path, content), "text/javascript")
            value += "#" + quote(uri, safe="")
            size += len(value)
            if size > 64 * 1024 * 1024 or len(self.imports) > 1024:
                raise AppDomainError("App 模块装配超过容量限制")
            self.imports[uri] = value
        result = render_manifest(self) if self.imports or self.maps.imports else ""
        if len(result.encode()) > 64 * 1024 * 1024:
            raise AppDomainError("App 模块清单超过容量限制")
        return result
