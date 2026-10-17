"""v5 HTML import map 收集；原 v1-v4 解析/摘要分支保持不变。"""

import posixpath
from typing import Any
from urllib.parse import quote

from app.services.app_asset_resolver import AppAssetResolver
from app.services.app_content_service import AppDomainError
from app.services.app_html_assembly import (
    HtmlAssetAssembler,
    render_module_entries,
    verify_integrity,
)
from app.services.app_module_graph_v5 import AppModuleGraphV5


class AdvancedHtmlAssetAssembler(HtmlAssetAssembler):
    """作者 map 先于模块处理，不依赖 Host 的多 import map 合并支持。"""

    def __init__(
        self, resolver: AppAssetResolver, entrypoint: str, **options: Any
    ) -> None:
        super().__init__(resolver, entrypoint, **options)
        self.modules = AppModuleGraphV5(resolver)
        self.author_capture: list[str] | None = None
        self.map_index: int | None = None
        self.module_seen = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """明确拒绝模块之后的 map、外部 map 和重复属性，不静默变更执行顺序。"""
        values = dict(attrs)
        if len(values) != len(attrs):
            raise AppDomainError("App HTML 不允许重复属性")
        kind = (values.get("type") or "").lower() if tag == "script" else ""
        preload = (
            tag == "link"
            and "modulepreload" in (values.get("rel") or "").lower().split()
        )
        if kind == "importmap":
            if self.module_seen or values.get("src"):
                raise AppDomainError("App import map 必须在模块之前内联声明")
            self.author_capture = []
            if self.map_index is None:
                self.map_index = len(self.parts)
            self.append("")
            return
        if kind == "module" or preload:
            self.module_seen = True
        if preload and values.get("href") and self._package_entry(values, "href"):
            return
        super().handle_starttag(tag, attrs)

    def _package_entry(self, attrs: dict, attribute: str) -> bool:
        reference = attrs[attribute]
        path = self.resolver.local_path(reference, self.entrypoint)
        if path is None:
            return False
        content = self.resolver.files[path]
        verify_integrity(content, attrs.get("integrity"))
        target = self.modules.maps.url(reference, self.modules.uri(self.entrypoint))
        uri = self.modules.add(path, content, identifier=target)
        self.module_entries[len(self.parts)] = (uri, attrs)
        self.append("")
        return True

    def _module_script(self, attrs: dict) -> bool:
        if (attrs.get("type") or "").lower() != "module" or not attrs.get("src"):
            return super()._module_script(attrs)
        if not self._package_entry(attrs, "src"):
            return False
        self.skip_script = True
        return True

    def handle_data(self, data: str) -> None:
        """保留原 map JSON 字节到校验阶段，不 eval 内联文本。"""
        if self.author_capture is not None:
            self.author_capture.append(data)
        else:
            super().handle_data(data)

    def handle_endtag(self, tag: str) -> None:
        """收齐 map 后使用固定 JSON 解析器，不将其作为 JS 执行。"""
        if tag == "script" and self.author_capture is not None:
            self.modules.configure_map("".join(self.author_capture), self.entrypoint)
            self.author_capture = None
        elif tag == "script" and self.module_capture is not None:
            attrs, parts = self.module_capture
            directory = posixpath.dirname(self.entrypoint)
            uri = (
                self.modules.prefix
                + (quote(directory, safe="/") + "/" if directory else "")
                + "%00inline-"
                + str(len(self.module_entries))
            )
            self.modules.add(self.entrypoint, "".join(parts).encode(), identifier=uri)
            self.module_entries[len(self.parts)] = (uri, attrs)
            self.append("")
            self.module_capture = None
        else:
            super().handle_endtag(tag)

    def rendered(self) -> str:
        """即使只有经典脚本引用作者 map，也交付可用的原生合并 map。"""
        if self.author_capture is not None or self.module_capture is not None:
            raise AppDomainError("App HTML 模块/map 缺少关闭标签")
        if self.map_index is not None:
            return render_module_entries(
                self.parts, self.module_entries, self.modules, map_index=self.map_index
            )
        return super().rendered()
