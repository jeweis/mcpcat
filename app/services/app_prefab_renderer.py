"""上传 Prefab 的官方 renderer 装配，JSON 是数据而非服务端代码。"""

import hashlib
import re
from importlib.metadata import version
from typing import Any

from prefab_ui import PrefabApp
from prefab_ui.renderer import get_renderer_csp, get_renderer_html

from app.models.app_review import AppCSP, AppPermissions
from app.services.app_content_service import AppDomainError, app_files
from app.services.app_package_validator import validate_prefab_json
from app.services.app_rendering import RenderedApp

PREFAB_VERSION = "0.20.2"


PrefabRendering = RenderedApp


def _map_tools(node: Any, tool_names: dict[str, str]) -> None:
    if isinstance(node, dict):
        if node.get("action") == "toolCall":
            alias = node.get("tool")
            if not isinstance(alias, str) or alias not in tool_names:
                raise AppDomainError("Prefab 动作引用未绑定工具")
            node["tool"] = tool_names[alias]
        for value in node.values():
            _map_tools(value, tool_names)
    elif isinstance(node, list):
        for value in node:
            _map_tools(value, tool_names)


def _metadata(info: dict) -> dict:
    approved = AppCSP.model_validate(info["csp"]).model_dump(
        by_alias=True, exclude_none=True
    )
    native = AppCSP.model_validate(get_renderer_csp("bundled")).model_dump(
        by_alias=True, exclude_none=True
    )
    for key, values in native.items():
        approved[key] = list(dict.fromkeys([*approved.get(key, []), *values]))
    permissions = AppPermissions.model_validate(info["permissions"]).model_dump(
        by_alias=True, exclude_none=True
    )
    return {"ui": {"csp": approved, "permissions": permissions}}


def prepare_prefab_app(
    info: dict, content: bytes, *, tool_names: dict[str, str]
) -> PrefabRendering:
    """使用固定官方 bundled HTML 和数据序列化，不执行上传内容。"""
    if (
        info["content_format"] != "prefab"
        or info["renderer_version"] != PREFAB_VERSION
        or version("prefab-ui") != PREFAB_VERSION
    ):
        raise AppDomainError("Prefab renderer 版本与审核版本不匹配")
    aliases = {item["alias"] for item in info["bindings"]}
    if aliases != set(tool_names) or any(
        not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value)
        for value in tool_names.values()
    ):
        raise AppDomainError("Prefab 工具映射与审核绑定不一致")
    files = app_files(info, content)
    wire = validate_prefab_json(files[info["entrypoint"]])
    _map_tools(wire, tool_names)
    # from_json 是公开纯数据构造接口；组件校验已在前面完成。
    payload = PrefabApp.from_json(wire).to_json()
    html = get_renderer_html("bundled")
    return PrefabRendering(
        html,
        payload,
        _metadata(info),
        hashlib.sha256(html.encode()).hexdigest(),
        PREFAB_VERSION,
    )
