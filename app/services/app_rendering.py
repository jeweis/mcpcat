"""Prefab/HTML 共用的派生发布入口，不替代原始下载制品。"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RenderedApp:
    """原始包不变，派生 HTML 记录内容摘要和固定装配版本。"""

    html: str
    structured_content: dict[str, Any]
    metadata: dict[str, Any]
    sha256: str
    renderer_version: str

    def payload_sha256(self) -> str:
        """动态定义/映射及 CSP 也属于冻结审核结果，不仅是 HTML。"""
        value = {"content": self.structured_content, "metadata": self.metadata}
        return hashlib.sha256(
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()


def app_callback_name(app_id: int, version: str, alias: str) -> str:
    """固定版本回调命名；装配和工具目录共用同一规则。"""
    digest = hashlib.sha256(f"{version}:{alias}".encode()).hexdigest()[:24]
    return f"app_{app_id}_{digest}"


def render_app(info: dict, content: bytes, app_id: int) -> RenderedApp:
    """发布与读取共用完整装配，读取时验证已审核入口未漂移。"""
    from app.services.app_content_service import AppDomainError
    from app.services.app_html_renderer import prepare_html_app
    from app.services.app_prefab_renderer import prepare_prefab_app

    names = {
        item["alias"]: app_callback_name(app_id, info["version"], item["alias"])
        for item in info["bindings"]
    }
    renderer = (
        prepare_prefab_app if info["content_format"] == "prefab" else prepare_html_app
    )
    rendered = renderer(info, content, tool_names=names)
    if info.get("rendered_sha256") is not None and (
        rendered.sha256 != info["rendered_sha256"]
        or rendered.renderer_version != info.get("rendering_version")
    ):
        raise AppDomainError("App 派生入口与发布审核不一致，请审核新版本")
    if (
        info.get("rendered_payload_sha256") is not None
        and rendered.payload_sha256() != info["rendered_payload_sha256"]
    ):
        raise AppDomainError("App 派生数据与发布审核不一致，请审核新版本")
    return rendered
