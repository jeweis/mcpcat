"""CSS AST 静态资产装配，不下载、不执行、不读取包外文件。"""

import base64

import pytest

from app.services.app_asset_resolver import AppAssetResolver
from app.services.app_content_service import AppDomainError
from app.services.app_css_assets import assemble_app_css


def test_css_urls_and_imports_use_package_relative_paths_without_mutating_bytes():
    files = {
        "styles/base.css": b'p{background:url("../images/bg.png")}',
        "images/bg.png": b"image bytes",
    }
    original = dict(files)
    resolver = AppAssetResolver(files, [])
    output = assemble_app_css(
        '@import "base.css" screen and (min-width: 1px); .a{content:"url(unchanged.png)"}',
        resolver=resolver,
        base="styles/main.css",
    )
    assert "@media screen and (min-width: 1px)" in output
    assert base64.b64encode(b"image bytes").decode() in output
    assert 'content:"url(unchanged.png)"' in output
    assert files == original


def test_nested_function_and_blocks_keep_css_structure():
    resolver = AppAssetResolver({"img.png": b"x"}, [])
    output = assemble_app_css(
        "@media screen { .a {background: linear-gradient(red,blue), url(img.png)} }",
        resolver=resolver,
        base="main.css",
    )
    assert "linear-gradient(red,blue)" in output
    assert "data:image/png;base64,eA==" in output


@pytest.mark.parametrize(
    "reference",
    [
        "../../secret",
        "/etc/passwd",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://unreviewed.example/a.png",
        "https://name:secret@example.com/a.png",
        "dir\\image.png",
        "../%00secret",
    ],
)
def test_asset_paths_or_unreviewed_external_urls_refused(reference):
    resolver = AppAssetResolver({}, ["https://example.com"])
    with pytest.raises(ValueError):
        resolver.data_uri(reference, "main.css")


def test_reviewed_external_asset_is_preserved_but_never_downloaded():
    resolver = AppAssetResolver({}, ["https://example.com"])
    css = '@import "https://example.com/base.css"; a{background:url(https://example.com/bg.png)}'
    output = assemble_app_css(css, resolver=resolver, base="main.css")
    assert "https://example.com/base.css" in output
    assert "https://example.com/bg.png" in output


def test_import_cycle_and_invalid_css_are_not_silently_ignored():
    resolver = AppAssetResolver(
        {"a.css": b'@import "b.css";', "b.css": b'@import "a.css";'}, []
    )
    with pytest.raises(AppDomainError, match="循环"):
        assemble_app_css(
            resolver.files["a.css"].decode(), resolver=resolver, base="a.css"
        )
    with pytest.raises(AppDomainError, match="语法"):
        assemble_app_css("}", resolver=resolver, base="a.css")


def test_asset_budget_is_checked_before_encoding_large_repeated_data():
    resolver = AppAssetResolver({"img.png": b"x"}, [])
    resolver.generated_bytes = 64 * 1024 * 1024
    with pytest.raises(AppDomainError, match="容量"):
        resolver.data_uri("img.png", "main.css")


def test_root_relative_and_encoded_file_references_resolve_only_known_files():
    resolver = AppAssetResolver({"images/a b.png": b"known"}, [])
    assert (
        resolver.local_path("/images/a%20b.png?v=1", "styles/main.css")
        == "images/a b.png"
    )


@pytest.mark.parametrize(
    "condition, expected",
    [
        ("layer(theme)", "@layer theme{a{color:red}}"),
        ("layer", "@layer {a{color:red}}"),
        ("supports(display:grid)", "@supports (display:grid){a{color:red}}"),
        (
            "layer(theme.dark) supports(display:grid) screen and (min-width:1px)",
            "@supports (display:grid){@media screen and (min-width:1px){@layer theme.dark{a{color:red}}}}",
        ),
        (
            "supports(selector(:has(.x)))",
            "@supports (selector(:has(.x))){a{color:red}}",
        ),
    ],
)
def test_import_layer_supports_and_media_keep_their_order(condition, expected):
    resolver = AppAssetResolver({"base.css": b"a{color:red}"}, [])
    assert (
        assemble_app_css(
            f'@import "base.css" {condition};', resolver=resolver, base="main.css"
        )
        == expected
    )


@pytest.mark.parametrize(
    "condition",
    [
        "layer()",
        "layer(1)",
        "layer(a.)",
        "supports()",
        "supports(display:grid) layer(theme)",
        "layer(a) layer(b)",
    ],
)
def test_invalid_import_conditions_do_not_become_unconditional_css(condition):
    resolver = AppAssetResolver({"base.css": b"a{color:red}"}, [])
    with pytest.raises(AppDomainError):
        assemble_app_css(
            f'@import "base.css" {condition};', resolver=resolver, base="main.css"
        )


def test_import_after_style_rule_is_rejected_instead_of_activated():
    resolver = AppAssetResolver({"base.css": b"a{color:red}"}, [])
    with pytest.raises(AppDomainError, match="之前"):
        assemble_app_css(
            'p{color:blue}@import "base.css";', resolver=resolver, base="main.css"
        )
    assert assemble_app_css(
        '@layer theme;@import "base.css" layer(theme);',
        resolver=resolver,
        base="main.css",
    ).startswith("@layer theme;@layer theme{")
