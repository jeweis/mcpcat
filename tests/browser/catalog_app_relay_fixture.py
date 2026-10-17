"""可信本机 Host/View 夹具：验证中继浏览器层，不冒充真实 MCP Provider。"""

import argparse
import html
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from app.services.catalog_app_identity import CatalogAppIdentity
from app.services.catalog_app_relay import relay_document

DIRECTORY = Path(__file__).parent
POLICY = "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-src about:; connect-src 'none'; object-src 'none'"


def fixture() -> str:
    """两个来源有相同 save/asset URI，但 Widget 与会话隔离。"""
    data = []
    script = (DIRECTORY / "catalog_app_relay_view.js").read_text()
    for index, label in enumerate(("left", "right"), 1):
        identity = CatalogAppIdentity(
            f"00000000-0000-0000-0000-{index:012d}",
            "12345678-1234-1234-1234-123456789abc",
        )
        original = (
            '<!doctype html><html><body><p id="result">Starting '
            + label
            + "</p><script>const label="
            + json.dumps(label)
            + ";"
            + script
            + "</script></body></html>"
        ).encode()
        document = relay_document(
            identity, original, {}, {"save": identity.callback("save")}, title=label
        )
        data.append(
            {
                "label": label,
                "html": document.html,
                "callback": identity.callback("save"),
                "resource": identity.resource("asset://same/item?q=1#fragment"),
            }
        )
    config = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c")
    return (
        '<!doctype html><html><body><h1>Catalog App relay browser fixture</h1><p id="status">Waiting</p><div id="views"></div><script type="application/json" id="fixture-config">'
        + config
        + '</script><script src="/host.js"></script></body></html>'
    )


class FixtureHandler(BaseHTTPRequestHandler):
    """只交付固定测试文件，不代理网络、上传文件或读取业务数据。"""

    def do_GET(self) -> None:
        """回环测试页执行可信夹具，所有资源均禁止外部网络。"""
        if self.path == "/host.js":
            content = (DIRECTORY / "catalog_app_relay_host.js").read_bytes()
            media = "text/javascript"
        elif self.path == "/":
            content, media = fixture().encode(), "text/html; charset=utf-8"
        elif self.path == "/opaque":
            # Host 自己也在 opaque sandbox：模拟真实 Host 的第二层隔离。
            host = fixture().replace(
                '<script src="/host.js"></script>',
                "<script>"
                + (DIRECTORY / "catalog_app_relay_host.js").read_text()
                + "</script>",
            )
            page = (
                "<!doctype html><h1>Opaque Host isolation fixture</h1>"
                '<iframe title="Opaque Host" sandbox="allow-scripts" '
                'style="width:100%;height:800px" srcdoc="'
                + html.escape(host, quote=True)
                + '"></iframe>'
            )
            content, media = page.encode(), "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", media)
        self.send_header("Content-Security-Policy", POLICY)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18764)
    args = parser.parse_args()
    HTTPServer(("127.0.0.1", args.port), FixtureHandler).serve_forever()
