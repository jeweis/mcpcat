"""HTML 静态装配保留原 JS 字节；不执行、下载或扩张 CSP。"""

import base64
import hashlib

import pytest

from app.services.app_asset_resolver import AppAssetResolver
from app.services.app_content_service import AppDomainError
from app.services.app_html_assembly import assemble_app_html


def test_self_contained_html_keeps_inline_script_and_entities():
    html = '<!DOCTYPE html><html><body><p>&amp;&#65;</p><script>window.x="literal";</script><svg><circle r="1"/></svg></body></html>'
    result = assemble_app_html(
        html, resolver=AppAssetResolver({}, []), entrypoint="index.html"
    )
    assert '<script>window.x="literal";</script>' in result
    assert "&amp;&#65;" in result
    assert '<circle r="1"></circle>' in result


def test_css_image_and_classic_script_embedded_without_rewriting_script(tmp_path):
    marker = tmp_path / "never-executed"
    script = f"// 中文\nconst text = String.raw`</script>`; // {marker}\n".encode()
    files = {
        "assets/app.js": script,
        "assets/app.css": b'body{background:url("icon.png")}',
        "assets/icon.png": b"image",
    }
    original = dict(files)
    html = '<html><head><link rel="stylesheet" href="assets/app.css"></head><body><img src="assets/icon.png"><script src="assets/app.js"></script></body></html>'
    result = assemble_app_html(
        html, resolver=AppAssetResolver(files, []), entrypoint="index.html"
    )
    assert base64.b64encode(script).decode() in result
    assert "data:image/png;base64,aW1hZ2U=" in result
    assert 'src="assets/app.js"' not in result
    assert "document.currentScript.replaceWith(node)" in result
    assert files == original
    assert not marker.exists()


def test_script_integrity_checked_before_inline_delivery():
    content = b"window.value=7;"
    integrity = "sha256-" + base64.b64encode(hashlib.sha256(content).digest()).decode()
    resolver = AppAssetResolver({"app.js": content}, [])
    assert "atob(" in assemble_app_html(
        f'<script src="app.js" integrity="{integrity}"></script>',
        resolver=resolver,
        entrypoint="index.html",
    )
    with pytest.raises(AppDomainError, match="integrity"):
        assemble_app_html(
            '<script src="app.js" integrity="sha256-invalid"></script>',
            resolver=resolver,
            entrypoint="index.html",
        )


@pytest.mark.parametrize(
    "html",
    [
        '<img src="../outside.png">',
        '<script src="https://unreviewed.example/app.js"></script>',
        '<base href="https://elsewhere.example/">',
        '<img src="one.png" src="two.png">',
        '<script src="app.js" type="module"></script>',
        '<script src="app.js" defer></script>',
        '<script src="app.js" async></script>',
    ],
)
def test_unsupported_or_unreviewed_assets_are_explicitly_rejected(html):
    with pytest.raises(ValueError):
        assemble_app_html(
            html,
            resolver=AppAssetResolver({"app.js": b"window.x=1;"}, []),
            entrypoint="index.html",
        )


def test_reviewed_external_reference_not_fetched_or_changed():
    html = '<script src="https://approved.example/app.js"></script>'
    assert (
        assemble_app_html(
            html,
            resolver=AppAssetResolver({}, ["https://approved.example"]),
            entrypoint="index.html",
        )
        == html
    )


def test_inline_style_elements_and_attributes_resolve_local_urls():
    html = '<style>p{background:url(bg.png)}</style><p style="background:url(bg.png)">Test</p>'
    result = assemble_app_html(
        html, resolver=AppAssetResolver({"bg.png": b"x"}, []), entrypoint="index.html"
    )
    assert result.count("data:image/png;base64,eA==") == 2


def test_integrity_uses_strongest_declared_supported_digest():
    from app.services.app_html_assembly import verify_integrity

    content = b"window.value=7;"
    weaker = "sha256-" + base64.b64encode(hashlib.sha256(content).digest()).decode()
    stronger = "sha512-" + base64.b64encode(hashlib.sha512(content).digest()).decode()
    with pytest.raises(AppDomainError, match="integrity"):
        verify_integrity(content, weaker + " sha512-invalid")
    verify_integrity(content, "sha256-invalid " + stronger)
    verify_integrity(content, stronger.replace("sha512", "SHA512"))
    with pytest.raises(AppDomainError, match="integrity"):
        verify_integrity(content, "sha512-不合法")
