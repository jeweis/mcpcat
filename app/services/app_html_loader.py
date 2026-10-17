"""生成固定客户端 DOM 装载器，上传脚本字节不在服务器求值。"""

import base64
import json
from html import escape

from app.services.app_content_service import AppDomainError


def native_script_element(content: bytes, attributes: dict) -> str:
    """用包内 data URL 交给浏览器原生调度，不模拟 defer/async 事件。"""
    if (attributes.get("type") or "").lower() == "module":
        raise AppDomainError("包内模块依赖装配仍待实现")
    encoded = base64.b64encode(content).decode("ascii")
    values = {
        key: value
        for key, value in attributes.items()
        if key not in {"src", "integrity", "crossorigin"}
    }
    values["src"] = "data:text/javascript;charset=utf-8;base64," + encoded
    text = " ".join(
        key if value is None else f'{key}="{escape(value, quote=True)}"'
        for key, value in values.items()
    )
    return "<script " + text + "></script>"


def inline_asset_loader(tag: str, content: bytes, attributes: dict) -> str:
    """用 textContent 交付原字节，避免 </script/style> 破坏 HTML 边界。"""
    if tag not in {"script", "style"}:
        raise AppDomainError("不支持的静态装载器类型")
    if tag == "script" and (
        attributes.get("type", "").lower() == "module"
        or "defer" in attributes
        or "async" in attributes
    ):
        raise AppDomainError("模块/defer/async 脚本装配仍待验收，请保留已内联脚本")
    encoded = base64.b64encode(content).decode("ascii")
    attrs = {
        name: value
        for name, value in attributes.items()
        if name not in {"src", "href", "rel", "integrity", "crossorigin"}
    }
    options = json.dumps(attrs, ensure_ascii=True).replace("<", "\\u003c")
    return (
        "<script>(()=>{const node=document.createElement(" + json.dumps(tag) + ");"
        "const attrs=" + options + ";for(const [key,value] of Object.entries(attrs))"
        "node.setAttribute(key,value===null?'':value);"
        "node.async=false;node.textContent=new TextDecoder().decode(Uint8Array.from(atob("
        + json.dumps(encoded)
        + "),c=>c.charCodeAt(0)));"
        "document.currentScript.replaceWith(node);})();</script>"
    )
