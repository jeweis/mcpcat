"""上传 HTML 静态装配；经典脚本保留字节，模块路径由语法树适配。"""

import base64
import hashlib
import hmac
from html import escape
from html.parser import HTMLParser

from app.services.app_asset_resolver import AppAssetResolver
from app.services.app_content_service import AppDomainError
from app.services.app_css_assets import assemble_app_css, assemble_app_css_value
from app.services.app_html_loader import inline_asset_loader, native_script_element
from app.services.app_module_graph import AppModuleGraph
from app.services.app_srcset_assets import assemble_app_srcset

HTML_VOID_ELEMENTS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)


def verify_integrity(content: bytes, value: str | None) -> None:
    """包内资源若声明 SRI，装配前仍核对原始字节。"""
    if not value:
        return
    ranks = {"sha256": 1, "sha384": 2, "sha512": 3}
    candidates = []
    for item in value.split():
        algorithm, separator, expected = item.partition("-")
        if algorithm.lower() in ranks:
            candidates.append((algorithm.lower(), separator, expected))
    if candidates:
        strongest = max(ranks[item[0]] for item in candidates)
        for algorithm, _, expected in candidates:
            if ranks[algorithm] != strongest:
                continue
            digest = base64.b64encode(hashlib.new(algorithm, content).digest()).decode()
            if expected.isascii() and hmac.compare_digest(digest, expected):
                return
    raise AppDomainError("App 资产 integrity 与原始内容不符")


class HtmlAssetAssembler(HTMLParser):
    """生成独立派生入口，原始附件仍只读、不可覆盖。"""

    def __init__(
        self,
        resolver: AppAssetResolver,
        entrypoint: str,
        *,
        responsive_images: bool = True,
        native_scripts: bool = False,
        module_scripts: bool = False,
    ) -> None:
        super().__init__(convert_charrefs=False)
        self.resolver, self.entrypoint = resolver, entrypoint
        self.parts: list[str] = []
        self.size = 0
        self.skip_script = False
        self.in_style = False
        self.responsive_images = responsive_images
        self.native_scripts = native_scripts
        self.modules = AppModuleGraph(resolver) if module_scripts else None
        self.module_entries: dict[int, tuple[str, dict]] = {}
        self.module_capture: tuple[dict, list[str]] | None = None

    def append(self, value: str) -> None:
        """拼接前限制容量，不积累无界派生 HTML。"""
        self.size += len(value.encode())
        if self.size > 64 * 1024 * 1024:
            raise AppDomainError("App 派生 HTML 超过容量限制")
        self.parts.append(value)

    def _style(self, attrs: dict) -> bool:
        if "stylesheet" not in (
            attrs.get("rel") or ""
        ).lower().split() or not attrs.get("href"):
            return False
        path = self.resolver.local_path(attrs["href"], self.entrypoint)
        if path is None:
            return False
        content = self.resolver.files[path]
        verify_integrity(content, attrs.get("integrity"))
        css = assemble_app_css(
            content.decode("utf-8"), resolver=self.resolver, base=path
        )
        self.append(inline_asset_loader("style", css.encode(), attrs))
        return True

    def _script(self, attrs: dict) -> bool:
        if not attrs.get("src"):
            return False
        path = self.resolver.local_path(attrs["src"], self.entrypoint)
        if path is None:
            return False
        content = self.resolver.files[path]
        content.decode("utf-8")
        verify_integrity(content, attrs.get("integrity"))
        self.append(
            native_script_element(content, attrs)
            if self.native_scripts
            else inline_asset_loader("script", content, attrs)
        )
        self.skip_script = True
        return True

    def _module_script(self, attrs: dict) -> bool:
        if self.modules is None or (attrs.get("type") or "").lower() != "module":
            return False
        if not attrs.get("src"):
            self.module_capture = (attrs, [])
            return True
        path = self.resolver.local_path(attrs["src"], self.entrypoint)
        if path is None:
            return False
        content = self.resolver.files[path]
        verify_integrity(content, attrs.get("integrity"))
        uri = self.modules.add(path, content)
        self.module_entries[len(self.parts)] = (uri, attrs)
        self.append("")
        self.skip_script = True
        return True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """只有明确资产属性改变，原内联脚本数据由 parser 原样保留。"""
        if len(dict(attrs)) != len(attrs):
            raise AppDomainError("App HTML 不允许重复属性")
        values = dict(attrs)
        if (
            tag == "link"
            and self.modules is not None
            and "modulepreload" in (values.get("rel") or "").lower().split()
            and values.get("href")
        ):
            path = self.resolver.local_path(values["href"], self.entrypoint)
            if path is not None:
                verify_integrity(self.resolver.files[path], values.get("integrity"))
                uri = self.modules.add(path, self.resolver.files[path])
                self.module_entries[len(self.parts)] = (uri, values)
                self.append("")
                return
        if tag == "script" and self._module_script(values):
            return
        if values.get("style"):
            values["style"] = assemble_app_css_value(
                values["style"], resolver=self.resolver, base=self.entrypoint
            )
        if tag == "style":
            self.in_style = True
        if tag == "base":
            raise AppDomainError("App base URI 必须由受控发布入口提供")
        if (
            tag == "link"
            and self._style(values)
            or tag == "script"
            and self._script(values)
        ):
            return
        self._responsive_assets(tag, values)
        if tag in {"img", "audio", "video", "source", "track"} and values.get("src"):
            values["src"] = self.resolver.data_uri(values["src"], self.entrypoint)
        if tag == "video" and values.get("poster"):
            values["poster"] = self.resolver.data_uri(values["poster"], self.entrypoint)
        if tag in {"iframe", "object", "embed"}:
            raise AppDomainError("App 嵌套框架/对象装配仍需单独 Host 审核")
        text = " ".join(
            name if value is None else f'{name}="{escape(value, quote=True)}"'
            for name, value in values.items()
        )
        self.append("<" + tag + (" " + text if text else "") + ">")

    def _responsive_assets(self, tag: str, values: dict) -> None:
        if not self.responsive_images:
            if "srcset" in values:
                raise AppDomainError("App srcset 资产装配仍待验收")
            return
        for attribute in ("srcset", "imagesrcset") if tag == "link" else ("srcset",):
            if attribute in values:
                values[attribute] = assemble_app_srcset(
                    values[attribute] or "",
                    resolver=self.resolver,
                    base=self.entrypoint,
                )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """HTML void 元素无需关闭，SVG/XML 形式保留对应结束标签。"""
        self.handle_starttag(tag, attrs)
        if tag not in HTML_VOID_ELEMENTS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.module_capture is not None:
            attrs, parts = self.module_capture
            path = self.entrypoint + f"#inline-{len(self.module_entries)}"
            uri = self.modules.add(path, "".join(parts).encode())
            self.module_entries[len(self.parts)] = (uri, attrs)
            self.append("")
            self.module_capture = None
            return
        if tag == "style":
            self.in_style = False
        if tag == "script" and self.skip_script:
            self.skip_script = False
            return
        self.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.module_capture is not None:
            self.module_capture[1].append(data)
            return
        if not self.skip_script:
            if self.in_style:
                data = assemble_app_css(
                    data, resolver=self.resolver, base=self.entrypoint
                )
            self.append(data)

    def handle_entityref(self, name: str) -> None:
        self.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.append(f"<!{decl}>")

    def rendered(self) -> str:
        """模块图完整后才交付入口，import map 放在第一个模块前。"""
        if self.module_capture is not None:
            raise AppDomainError("App HTML 内联模块缺少关闭标签")
        if not self.module_entries:
            return "".join(self.parts)
        return render_module_entries(self.parts, self.module_entries, self.modules)


def render_module_entries(
    parts: list[str],
    entries: dict,
    modules: AppModuleGraph,
    *,
    map_index: int | None = None,
) -> str:
    """由已完成的模块映射生成原生入口标签，不执行或下载模块。"""
    mapping = modules.render()
    size = sum(len(part.encode()) for part in parts) + len(mapping.encode())
    for index, (uri, attrs) in entries.items():
        values = {
            key: value
            for key, value in attrs.items()
            if key not in {"src", "integrity", "crossorigin"}
        }
        is_link = "modulepreload" in (attrs.get("rel") or "").lower().split()
        source = modules.imports[uri]
        # 重复入口不能先生成无界字符串、最后才检查 join 后总容量。
        size += (
            len(source)
            + sum(len(str(key)) + 6 * len(str(value)) for key, value in values.items())
            + 64
        )
        if size > 64 * 1024 * 1024:
            raise AppDomainError("App 派生 HTML 超过容量限制")
        values["href" if is_link else "src"] = source
        text = " ".join(
            key if value is None else f'{key}="{escape(value, quote=True)}"'
            for key, value in values.items()
        )
        parts[index] = (
            "<link " + text + ">" if is_link else "<script " + text + "></script>"
        )
    first = min(entries) if map_index is None else map_index
    parts[first] = mapping + parts[first]
    result = "".join(parts)
    if len(result.encode()) > 64 * 1024 * 1024:
        raise AppDomainError("App 派生 HTML 超过容量限制")
    return result


def assemble_app_html(
    html: str,
    *,
    resolver: AppAssetResolver,
    entrypoint: str,
    responsive_images: bool = True,
    native_scripts: bool = False,
    module_scripts: bool = False,
    module_version: int = 4,
) -> str:
    """固定解析流程只装配，不在服务器运行上传前端或构建脚本。"""
    assembler_type = HtmlAssetAssembler
    if module_scripts and module_version == 5:
        from app.services.app_html_advanced import AdvancedHtmlAssetAssembler

        assembler_type = AdvancedHtmlAssetAssembler
    assembler = assembler_type(
        resolver,
        entrypoint,
        responsive_images=responsive_images,
        native_scripts=native_scripts,
        module_scripts=module_scripts,
    )
    assembler.feed(html)
    assembler.close()
    if assembler.skip_script:
        raise AppDomainError("App HTML 外部脚本缺少关闭标签")
    return assembler.rendered()
