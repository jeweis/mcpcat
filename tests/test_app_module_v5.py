"""v5 计算型模块/映射/URL 身份以及冻结 v4 的独立边界。"""

import base64
import json
from urllib.parse import unquote

import pytest
from test_app_module_graph import ModuleTags

from app.services.app_asset_resolver import AppAssetResolver
from app.services.app_html_assembly import assemble_app_html
from app.services.app_module_graph_v5 import AppModuleGraphV5
from app.services.app_module_maps import ModuleImportMaps


def decoded(value):
    return base64.b64decode(value.split(",", 1)[1].split("#", 1)[0]).decode()


def render(html, files):
    return assemble_app_html(
        html,
        resolver=AppAssetResolver(files, []),
        entrypoint="web/index.html",
        native_scripts=True,
        module_scripts=True,
        module_version=5,
    )


def test_scoped_exact_prefix_null_and_first_map_precedence():
    graph = AppModuleGraphV5(AppAssetResolver({}, []))
    maps = graph.maps
    maps.add(
        json.dumps(
            {
                "imports": {"name": "./one.js", "pkg/": "./lib/", "blocked": None},
                "scopes": {
                    "./feature/": {"name": "./two.js"},
                    "./feature/deep/": {"extra": "./three.js"},
                },
            }
        ),
        "web/index.html",
    )
    maps.add('{"imports":{"name":"./ignored.js"}}', "web/index.html")
    assert maps.resolve("name", graph.uri("web/main.js")) == graph.uri("web/one.js")
    assert maps.resolve("name", graph.uri("web/feature/deep/main.js")) == graph.uri(
        "web/two.js"
    )
    assert maps.resolve("extra", graph.uri("web/feature/deep/main.js")) == graph.uri(
        "web/three.js"
    )
    assert maps.resolve("pkg/module.js", graph.uri("web/main.js")) == graph.uri(
        "web/lib/module.js"
    )
    with pytest.raises(ValueError, match="阻止"):
        maps.resolve("blocked", graph.uri("web/main.js"))


def test_computed_import_and_asset_nested_expression_preserved_once():
    files = {
        "web/main.js": b'const name="./lazy.js"; const mod=await import(/* comment */name); const asset="./icon.svg"; new URL(asset, import.meta.url);',
        "web/lazy.js": b"export const value=7;",
        "web/icon.svg": b"<svg/>",
    }
    graph = AppModuleGraphV5(AppAssetResolver(files, []))
    uri = graph.add("web/main.js", files["web/main.js"])
    manifest = graph.render()
    source = decoded(graph.imports[uri])
    assert "importModule as" in source and "assetURL as" in source
    assert "const mod=await __mcpcat_" in source
    assert "/* comment */name" in source
    assert "new URL(__mcpcat_" in source
    assert graph.uri("web/lazy.js") in graph.imports
    assert "data:image/svg+xml;base64," in manifest
    assert unquote(graph.imports[uri].split("#", 1)[1]) == uri


def test_query_fragment_distinct_instances_and_meta_url_runtime_identity():
    graph = AppModuleGraphV5(
        AppAssetResolver({"web/dep.js": b"export default import.meta.url;"}, [])
    )
    a = graph.reference("./dep.js?one#first", graph.uri("web/main.js"))
    b = graph.reference("./dep.js?two#second", graph.uri("web/main.js"))
    graph.render()
    assert a != b and graph.imports[a] != graph.imports[b]
    assert decoded(graph.imports[a]) == decoded(graph.imports[b])
    assert unquote(graph.imports[a].split("#", 1)[1]) == a


def test_query_scope_is_precompiled_for_unknown_computed_fragment():
    files = {
        "web/entry.js": b'import value from "chosen"; export default value;',
        "web/one.js": b"export default 1;",
        "web/two.js": b"export default 2;",
    }
    graph = AppModuleGraphV5(AppAssetResolver(files, []))
    graph.configure_map(
        '{"imports":{"chosen":"./one.js"},"scopes":{"./entry.js?special":{"chosen":"./two.js"}}}',
        "web/index.html",
    )
    graph.render()
    specialized = graph.uri("web/entry.js") + "?special"
    assert graph.uri("web/two.js") in decoded(graph.imports[specialized])
    assert graph.uri("web/one.js") in decoded(graph.imports[graph.uri("web/entry.js")])


def test_author_inline_map_normalized_before_native_module_and_preload():
    files = {
        "web/main.js": b'import value from "alias"; document.body.textContent=value;',
        "web/dep.js": b"export default 7;",
    }
    html = '<script type="importmap">{"imports":{"alias":"./dep.js"}}</script><link rel="modulepreload" href="main.js?v=1"><script type="module" src="main.js?v=1"></script>'
    result = render(html, files)
    assert result.count('type="importmap"') == 1
    assert 'href="main.js?v=1"' not in result
    assert "data-mcpcat-module-config=" in result
    parsed = ModuleTags()
    parsed.feed(result)
    assert json.loads(parsed.mapping)["imports"]["alias"].startswith("data:")
    source = next(tag["src"] for tag in parsed.tags if tag.get("type") == "module")
    assert "?v=1" in unquote(source.split("#", 1)[1])


@pytest.mark.parametrize(
    "mapping",
    [
        '{"imports":{"x":"../../outside.js"}}',
        '{"imports":{"x":"https://unreviewed.example/x.js"}}',
        '{"imports":{"x":"./one.js","x":"./two.js"}}',
        '{"imports":{"x/":"./not-directory.js"}}',
        '{"imports":{"x":false}}',
        '{"scopes":[]}',
        '{"unknown":true}',
    ],
)
def test_invalid_author_maps_fail_before_delivery(mapping):
    maps = ModuleImportMaps("mcpcat-app://fixture/", AppAssetResolver({}, []))
    with pytest.raises(ValueError):
        maps.add(mapping, "index.html")


@pytest.mark.parametrize(
    "html",
    [
        '<script type="module"></script><script type="importmap">{}</script>',
        '<script type="importmap" src="map.json"></script>',
        '<script type="importmap">{}',
    ],
)
def test_late_external_or_unclosed_map_is_explicit_error(html):
    with pytest.raises(ValueError):
        render(html, {})


def test_inline_module_reserved_identity_does_not_collide_with_uploaded_filename():
    html = '<script type="module" src="index.html%23inline-1"></script><script type="module">export const inline=2;</script>'
    files = {"web/index.html#inline-1": b"export const original=1;"}
    result = render(html, files)
    assert "data-mcpcat-module-config" in result
    parsed = ModuleTags()
    parsed.feed(result)
    sources = [tag["src"] for tag in parsed.tags if tag.get("type") == "module"]
    assert len(sources) == 2 and sources[0] != sources[1]
    assert "original=1" in decoded(sources[0]) and "inline=2" in decoded(sources[1])


def test_no_module_but_author_map_still_supports_classic_dynamic_import():
    result = render(
        '<script type="importmap">{"imports":{"alias":"./dep.js?v=1"}}</script><script>import("alias");</script>',
        {"web/dep.js": b"export default 1;"},
    )
    parsed = ModuleTags()
    parsed.feed(result)
    assert json.loads(parsed.mapping)["imports"]["alias"].startswith("data:")
    assert 'import("alias");' in result


def test_many_prefixes_cannot_expand_unbounded_native_manifest():
    graph = AppModuleGraphV5(AppAssetResolver({}, []))
    graph.imports = {
        graph.uri(f"item-{index}.js"): "data:text/javascript;base64,eA=="
        for index in range(128)
    }
    graph.maps.imports = {f"alias-{index}/": graph.prefix for index in range(33)}
    with pytest.raises(ValueError, match="映射数量"):
        graph.render()


def test_duplicate_large_aliases_reject_before_serializing_payload(monkeypatch):
    import app.services.app_module_manifest as manifest

    graph = AppModuleGraphV5(AppAssetResolver({}, []))
    uri = graph.uri("module.js")
    graph.imports[uri] = "data:" + "x" * (20 * 1024 * 1024)
    graph.maps.imports = {"first": uri, "second": uri}
    original = manifest.encoded_manifest

    def serialized(value):
        assert "modules" not in value, "full oversized payload must not be serialized"
        return original(value)

    monkeypatch.setattr(manifest, "encoded_manifest", serialized)
    with pytest.raises(ValueError, match="容量"):
        graph.render()


def test_author_map_entry_count_has_explicit_bound():
    maps = ModuleImportMaps("mcpcat-app://fixture/", AppAssetResolver({}, []))
    with pytest.raises(ValueError, match="数量"):
        maps.add(
            json.dumps(
                {"imports": {f"name-{index}": "./file.js" for index in range(2049)}}
            ),
            "index.html",
        )


def test_separate_import_maps_cannot_bypass_aggregate_entry_limit():
    maps = ModuleImportMaps("mcpcat-app://fixture/", AppAssetResolver({}, []))
    maps.add(
        json.dumps(
            {"imports": {f"first-{index}": "./file.js" for index in range(1024)}}
        ),
        "index.html",
    )
    with pytest.raises(ValueError, match="数量"):
        maps.add(
            json.dumps(
                {"imports": {f"second-{index}": "./file.js" for index in range(1025)}}
            ),
            "index.html",
        )
