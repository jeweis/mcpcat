"""HTML 上传版本装配为独立 UI，不运行用户后端或构建命令。"""

import hashlib

from app.models.app_review import AppCSP, AppPermissions
from app.services.app_asset_resolver import AppAssetResolver
from app.services.app_content_service import AppDomainError, app_files
from app.services.app_html_assembly import assemble_app_html
from app.services.app_rendering import RenderedApp


def _assembly_version(info: dict) -> str:
    value = info.get("rendering_version") or (
        "html-assembly-1"
        if info.get("status") in {"published", "deprecated"}
        else "html-assembly-5"
    )
    if value not in {
        "html-assembly-1",
        "html-assembly-2",
        "html-assembly-3",
        "html-assembly-4",
        "html-assembly-5",
    }:
        raise AppDomainError("HTML 装配版本不受支持，请审核新版本")
    return value


def prepare_html_app(
    info: dict, content: bytes, *, tool_names: dict[str, str]
) -> RenderedApp:
    """返回派生 HTML/摘要及公开绑定映射，完全不包含网关凭证。"""
    if info["content_format"] != "html":
        raise AppDomainError("不是 HTML App")
    if {item["alias"] for item in info["bindings"]} != set(tool_names):
        raise AppDomainError("HTML 工具映射与审核绑定不一致")
    files = app_files(info, content)
    csp = AppCSP.model_validate(info["csp"])
    permissions = AppPermissions.model_validate(info["permissions"])
    resolver = AppAssetResolver(files, csp.resource_domains or [])
    assembly_version = _assembly_version(info)
    html = assemble_app_html(
        files[info["entrypoint"]].decode("utf-8"),
        resolver=resolver,
        entrypoint=info["entrypoint"],
        responsive_images=assembly_version != "html-assembly-1",
        native_scripts=assembly_version
        in {"html-assembly-3", "html-assembly-4", "html-assembly-5"},
        module_scripts=assembly_version in {"html-assembly-4", "html-assembly-5"},
        module_version=5 if assembly_version == "html-assembly-5" else 4,
    )
    policy = csp.model_dump(by_alias=True, exclude_none=True)
    if assembly_version in {"html-assembly-3", "html-assembly-4", "html-assembly-5"}:
        policy["resourceDomains"] = [*policy.get("resourceDomains", []), "data:"]
    return RenderedApp(
        html,
        {"mcpcat": {"format": "html", "toolBindings": tool_names}},
        {
            "ui": {
                "csp": policy,
                "permissions": permissions.model_dump(by_alias=True, exclude_none=True),
            }
        },
        hashlib.sha256(html.encode()).hexdigest(),
        assembly_version,
    )
