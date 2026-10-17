"""固定 Catalog 来源中继壳；上游 HTML 原文只编码为数据，不在服务器执行。"""

import base64
import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.services.catalog_app_identity import CatalogAppIdentity

MAX_HTML_BYTES = 16 * 1024 * 1024
CALLBACK = re.compile(r"catapp_[0-9a-f]{48}")
PERMISSIONS = {
    "camera": "camera",
    "microphone": "microphone",
    "geolocation": "geolocation",
    "clipboardWrite": "clipboard-write",
}


@dataclass(frozen=True)
class CatalogRelayDocument:
    """派生壳与原始 HTML 分开标识，不伪造原来源内容摘要。"""

    html: str
    metadata: dict
    original_sha256: str


def relay_metadata(metadata: dict) -> tuple[dict, str, str]:
    """仅外层增加 about: 容器；内层恢复原 frame-src，其他策略继承 Host。"""
    result = copy.deepcopy(metadata)
    ui = result.setdefault("ui", {})
    if not isinstance(ui, dict):
        raise ValueError("Invalid upstream App metadata")
    csp = ui.setdefault("csp", {})
    if not isinstance(csp, dict):
        raise ValueError("Invalid upstream App CSP")
    frames = csp.get("frameDomains") or []
    if not isinstance(frames, list) or len(frames) > 256:
        raise ValueError("Invalid upstream App frame policy")
    for value in frames:
        if (
            not isinstance(value, str)
            or len(value) > 4096
            or not (
                value in {"'self'", "'none'"}
                or re.fullmatch(r"[A-Za-z0-9*._~:/?#\[\]@!$&()+,=%-]+", value)
            )
        ):
            raise ValueError("Invalid upstream App frame policy")
    inner = (
        "frame-src "
        + (" ".join(frames) if frames else "'none'")
        + "; object-src 'none'"
    )
    csp["frameDomains"] = list(dict.fromkeys([*frames, "about:"]))
    permissions = ui.get("permissions") or {}
    if not isinstance(permissions, dict):
        raise ValueError("Invalid upstream App permissions")
    if any(value != {} for key, value in permissions.items() if key in PERMISSIONS):
        raise ValueError("Invalid upstream App permissions")
    allow = "; ".join(
        feature + " *" for key, feature in PERMISSIONS.items() if key in permissions
    )
    return result, inner, allow


def _fixed_module() -> str:
    directory = Path(__file__).parent
    fixed = "\n".join(
        (directory / name).read_text()
        for name in ("catalog_app_relay_core.js", "catalog_app_relay_boot.js")
    )
    if "</script" in fixed.lower():
        raise ValueError("Fixed App relay contains unsafe HTML boundary")
    return fixed


def relay_document(
    identity: CatalogAppIdentity,
    original_html: bytes,
    metadata: dict,
    tools: dict[str, str],
    *,
    title: str = "Upstream MCP App",
) -> CatalogRelayDocument:
    """自有固定 JS 与上游字节分离，不拼接用户 JS 到执行代码。"""
    if not original_html or len(original_html) > MAX_HTML_BYTES:
        raise ValueError("Upstream App HTML exceeds limit")
    original_html.decode("utf-8")
    if (
        not isinstance(tools, dict)
        or len(tools) > 4096
        or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or not CALLBACK.fullmatch(value)
            for key, value in tools.items()
        )
    ):
        raise ValueError("Invalid App callback bindings")
    result, inner, allow = relay_metadata(metadata)
    config = {
        "source": identity.source,
        "generation": identity.generation,
        "tools": tools,
        "title": title[:256],
        "innerPolicy": inner,
        "allow": allow,
        "html": base64.b64encode(original_html).decode("ascii"),
    }
    config_json = json.dumps(config, sort_keys=True, ensure_ascii=True).replace(
        "<", "\\u003c"
    )
    html = (
        '<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;width:100%;}iframe{width:100%;min-height:240px;border:0;}</style></head><body><script type="application/json" id="mcpcat-source-config">'
        + config_json
        + '</script><script type="module">'
        + _fixed_module()
        + "</script></body></html>"
    )
    if len(html.encode()) > 64 * 1024 * 1024:
        raise ValueError("Derived App HTML exceeds limit")
    return CatalogRelayDocument(html, result, hashlib.sha256(original_html).hexdigest())
