"""测试台协议操作：只通过当前 MCP 客户端，不访问管理索引。"""

import json
from typing import Any

from fastmcp.resources.template import expand_uri_template
from mcp import types as mcp_types

from app.services.inspector_skills import MAX_BYTES, capabilities

UI_EXTENSION = "io.modelcontextprotocol/ui"


def dump(value: Any) -> dict:
    """保持协议字段别名与 JSON 类型，并限制结果体积。"""
    data = value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if len(json.dumps(data).encode()) > MAX_BYTES:
        raise ValueError("响应超过测试台 16 MiB 限制")
    return data


async def listing(client: Any, kind: str, cursor: str | None = None) -> dict:
    """读取一页，区分未提供能力与支持但空。"""
    capability = {"templates": "resources", "apps": "tools"}.get(kind, kind)
    if capability not in capabilities(client)["capabilities"]:
        return {"supported": False, "items": [], "nextCursor": None}
    method, field = {
        "resources": (client.list_resources_mcp, "resources"),
        "templates": (client.list_resource_templates_mcp, "resourceTemplates"),
        "prompts": (client.list_prompts_mcp, "prompts"),
        "apps": (client.list_tools_mcp, "tools"),
    }[kind]
    result = dump(await method(cursor=cursor))
    items = result.get(field, [])
    if kind == "apps":
        items = [item for item in items if app_uri(item)]
    return {"supported": True, "items": items,
            "nextCursor": result.get("nextCursor")}


def app_uri(tool: dict) -> str | None:
    """从工具公开 UI 关联读取入口，不猜测数据库 slug。"""
    metadata = tool.get("_meta") or {}
    uri = (metadata.get("ui") or {}).get("resourceUri")
    uri = uri or metadata.get("ui/resourceUri")
    return uri if isinstance(uri, str) and uri.startswith("ui://") else None


async def read_resource(client: Any, uri: str) -> dict:
    """保留所有资源块；HTML/脚本只作为文本返回。"""
    result = await client.read_resource_mcp(uri)
    return dump(result)


async def read_template(client: Any, template: str, arguments: dict) -> dict:
    """使用固定 SDK 的 URI 模板展开，不进行字符串拼接。"""
    uri = expand_uri_template(template, arguments)
    if "{" in uri or "}" in uri or len(uri) > 4096:
        raise ValueError("请填写完整的资源模板参数")
    return {"uri": uri, **await read_resource(client, uri)}


async def get_prompt(client: Any, name: str, arguments: dict) -> dict:
    """获取消息模板，不调用模型。"""
    return dump(await client.get_prompt_mcp(name, arguments))


async def complete_argument(
    client: Any, kind: str, reference: str, argument: str,
    value: str, context: dict[str, str],
) -> dict:
    """通过原生协议获取候选，保留上下文且不执行模板或工具。"""
    if kind not in {"prompts", "templates"}:
        raise ValueError("仅提示和资源模板支持参数补全")
    if "completions" not in capabilities(client)["capabilities"]:
        return {"supported": False}
    ref = (
        mcp_types.PromptReference(type="ref/prompt", name=reference)
        if kind == "prompts" else
        mcp_types.ResourceTemplateReference(type="ref/resource", uri=reference)
    )
    result = await client.complete_mcp(
        ref, {"name": argument, "value": value}, context_arguments=context,
    )
    return {"supported": True, **dump(result)}


async def app_preview(client: Any, tool_name: str) -> dict:
    """从实时端点重新确认工具与 UI 资源，不预调用入口工具。"""
    tools = await client.list_tools()
    tool = next((dump(t) for t in tools if t.name == tool_name), None)
    uri = app_uri(tool or {})
    if not uri:
        raise ValueError("该工具没有可用的应用界面")
    resource = await read_resource(client, uri)
    blocks = resource.get("contents", [])
    if len(blocks) != 1 or blocks[0].get("uri") != uri:
        raise ValueError("应用资源响应与请求不一致")
    block = blocks[0]
    if block.get("mimeType") != "text/html;profile=mcp-app":
        raise ValueError("该资源不是可预览的 MCP 应用")
    if not isinstance(block.get("text"), str):
        raise ValueError("应用入口必须提供 HTML 文本")
    # UI-only 工具可能不出现在普通列表中。当前测试台只授权已发现工具，
    # 不因上游界面声明而扩大调用范围。
    allowed = [t.name for t in tools]
    return {
        "slug": tool_name, "version": "当前端点", "mode": "mock",
        "html": block["text"], "initial_result": {},
        "allowed_tools": allowed, "real_callable": True,
        "requested_metadata": block.get("_meta", {}),
        "tools": [{"name": name, "alias": name,
                   "server_name": "当前 MCP 端点", "tool_name": name}
                  for name in allowed],
    }


async def app_call(client: Any, entry: str, name: str, arguments: dict) -> dict:
    """每次重新确认入口与工具仍可见，不沿用过期允许列表。"""
    tools = await client.list_tools()
    if not any(t.name == entry and app_uri(dump(t)) for t in tools):
        raise ValueError("应用入口已不可用，请重新打开")
    if not any(t.name == name for t in tools):
        raise ValueError("工具不在当前端点可见范围内")
    return dump(await client.call_tool_mcp(name, arguments))


async def app_open(client: Any, entry: str, arguments: dict) -> dict:
    """显式确认后才调用入口，将动态结果交给隔离渲染器。"""
    preview = await app_preview(client, entry)
    preview["initial_result"] = await app_call(client, entry, entry, arguments)
    preview["initial_result_is_mcp"] = True
    preview["initial_arguments"] = arguments
    return preview
