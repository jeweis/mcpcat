"""模块图静态路径、循环身份和原始制品边界。"""

import base64
import json
from html.parser import HTMLParser

import pytest

from app.services.app_asset_resolver import AppAssetResolver
from app.services.app_html_assembly import assemble_app_html
from app.services.app_module_graph import AppModuleGraph


class ModuleTags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.mapping = [], ""
        self.in_map = False

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.tags.append(dict(attrs))
            self.in_map = dict(attrs).get("type") == "importmap"

    def handle_data(self, data):
        if self.in_map:
            self.mapping += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_map = False


def decoded(value):
    return base64.b64decode(value.split(",", 1)[1]).decode()


def test_graph_cycles_shared_identity_and_only_ast_paths_rewritten():
    files = {
        "js/main.js": b'import {b} from "./b.js"; export * from "./b.js"; const text="import(\'./not-file\')"; const re=/import\("fake"\)/; // import "fake"\nconsole.log(b);',
        "js/b.js": b'import "./main.js"; export const b=2;',
    }
    original = dict(files)
    graph = AppModuleGraph(AppAssetResolver(files, []))
    entry = graph.add("js/main.js", files["js/main.js"])
    result = graph.render()
    assert len(graph.imports) == 2
    assert graph.uri("js/b.js") in decoded(graph.imports[entry])
    assert "const text=\"import('./not-file')\"" in decoded(graph.imports[entry])
    assert 'const re=/import\("fake"\)/' in decoded(graph.imports[entry])
    assert entry in decoded(graph.imports[graph.uri("js/b.js")])
    assert (
        json.loads(result.split(">", 1)[1].split("</script>")[0])["imports"]
        == graph.imports
    )
    assert files == original


def test_module_entry_inline_dynamic_json_and_asset_urls():
    files = {
        "web/main.mjs": 'const module=await import("./动态.js"); const asset=new URL("../icon.svg", import.meta.url);'.encode(),
        "web/动态.js": b'import data from "../data.json" with { type: "json" }; export default data;',
        "data.json": b'{"value":2}',
        "icon.svg": b"<svg/>",
    }
    html = '<!doctype html><html><head></head><body><script type="module" id="root" src="web/main.mjs"></script><script type="module">import "./web/main.mjs";</script></body></html>'
    result = assemble_app_html(
        html,
        resolver=AppAssetResolver(files, []),
        entrypoint="index.html",
        native_scripts=True,
        module_scripts=True,
    )
    parsed = ModuleTags()
    parsed.feed(result)
    mapping = json.loads(parsed.mapping)["imports"]
    assert len(mapping) == 4  # 三个包内模块/JSON，加一个内联入口。
    root = next(tag for tag in parsed.tags if tag.get("id") == "root")
    assert "data:image/svg+xml;base64," in decoded(root["src"])
    assert "mcpcat-app://" in decoded(root["src"])
    assert all(tag.get("src", "data:").startswith("data:") for tag in parsed.tags)
    assert result.startswith("<!doctype html>")


@pytest.mark.parametrize(
    "source",
    [
        b'import "../../outside.js";',
        b'import "./missing.js";',
        b'import "https://unreviewed.example/module.js";',
        b"const value = ;",
        b"import(variable);",
        b"const a = <div/>;",
    ],
)
def test_module_failures_are_explicit_not_partial_silent_delivery(source):
    graph = AppModuleGraph(AppAssetResolver({"main.js": source}, []))
    graph.add("main.js", source)
    with pytest.raises(ValueError):
        graph.render()


def test_legacy_native_assembly_keeps_rejecting_modules():
    with pytest.raises(ValueError, match="模块"):
        assemble_app_html(
            '<script type="module" src="main.js"></script>',
            resolver=AppAssetResolver({"main.js": b"export const x=1;"}, []),
            entrypoint="index.html",
            native_scripts=True,
        )


def test_identical_bytes_in_distinct_modules_keep_distinct_identity():
    files = {"a.js": b"window.count++;", "b.js": b"window.count++;"}
    graph = AppModuleGraph(AppAssetResolver(files, []))
    a = graph.add("a.js", files["a.js"])
    b = graph.add("b.js", files["b.js"])
    graph.render()
    assert graph.imports[a] != graph.imports[b]


def test_modulepreload_uses_same_derived_resource_as_entry():
    files = {"app.js": b"export const value=1;"}
    result = assemble_app_html(
        '<link rel="modulepreload" href="app.js"><script type="module" src="app.js"></script>',
        resolver=AppAssetResolver(files, []),
        entrypoint="index.html",
        native_scripts=True,
        module_scripts=True,
    )
    assert 'href="app.js"' not in result
    assert 'href="data:text/javascript;base64,' in result


def test_json_wrapper_does_not_turn_proto_key_into_object_initializer():
    graph = AppModuleGraph(
        AppAssetResolver({"data.json": b'{"__proto__":{"safe":1}}'}, [])
    )
    uri = graph.add("data.json", graph.resolver.files["data.json"])
    graph.render()
    assert "JSON.parse(" in decoded(graph.imports[uri])


def test_duplicate_large_module_entries_reject_before_unbounded_join(monkeypatch):
    from app.services.app_html_assembly import render_module_entries

    monkeypatch.setattr(AppModuleGraph, "render", lambda self: "<script></script>")
    graph = AppModuleGraph(AppAssetResolver({}, []))
    graph.imports["same"] = "data:" + "x" * (33 * 1024 * 1024)
    with pytest.raises(ValueError, match="容量"):
        render_module_entries(["", ""], {0: ("same", {}), 1: ("same", {})}, graph)
