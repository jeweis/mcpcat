"""经典脚本交给浏览器原生调度，服务器只校验和编码原字节。"""

import base64
from html.parser import HTMLParser

import pytest

from app.services.app_asset_resolver import AppAssetResolver
from app.services.app_html_assembly import assemble_app_html


class Scripts(HTMLParser):
    """观察最终 parser-inserted 标签，而不是执行其内容。"""

    def __init__(self):
        super().__init__()
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.scripts.append(dict(attrs))


@pytest.mark.parametrize("attributes", ["defer", "async", "async defer", ""])
def test_native_script_preserves_bytes_flags_and_attributes(attributes):
    raw = "var value=String.raw`中文 </script> 😀`;".encode()
    html = f'<script id="fixture" {attributes} src="app.js"></script>'
    rendered = assemble_app_html(
        html,
        resolver=AppAssetResolver({"app.js": raw}, []),
        entrypoint="index.html",
        native_scripts=True,
    )
    parsed = Scripts()
    parsed.feed(rendered)
    assert len(parsed.scripts) == 1
    attrs = parsed.scripts[0]
    assert attrs["id"] == "fixture"
    for flag in ("async", "defer"):
        assert (flag in attrs) == (flag in attributes)
    assert attrs["src"].startswith("data:text/javascript;charset=utf-8;base64,")
    assert base64.b64decode(attrs["src"].split(",", 1)[1]) == raw
    assert "setTimeout" not in rendered and "replaceWith" not in rendered


def test_native_script_still_checks_integrity_and_blocks_unresolved_modules():
    for attributes in ('integrity="sha256-invalid"', 'type="module"'):
        with pytest.raises(ValueError):
            assemble_app_html(
                f'<script {attributes} src="app.js"></script>',
                resolver=AppAssetResolver({"app.js": b"export const x=1"}, []),
                entrypoint="index.html",
                native_scripts=True,
            )
