"""Apps 中心的来源目录发现，不复制上游内容或执行入口工具。"""

import asyncio
import json
import re
from copy import deepcopy
from urllib.parse import quote

import anyio
from fastapi import HTTPException, Request
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.client.extension import advertise

from app.models.mcp_config import PermissionType
from app.services.app_connections import app_public_base_url
from app.services.config_service import ConfigService
from app.services.inspector_service import McpcatAuth
from app.services.inspector_workbench import app_uri, listing
from app.services.security_service import security_service

_operations = asyncio.Semaphore(8)


def _key(request: Request) -> str:
    """长查询后也重新验证身份，不让公开来源成为撤权后的降级路径。"""
    value = request.headers.get(security_service.get_auth_header_name(), "")
    key = security_service.verify_api_key(value)
    if not key or not security_service.has_permission(key, PermissionType.READ):
        raise HTTPException(403, "当前身份无权读取应用目录")
    return value


def _source(request: Request, name: str) -> tuple[dict, dict]:
    """只接受已有用户服务，不枚举系统聚合入口或接受任意连接 URL。"""
    config = ConfigService.get_server_config(name)
    info = request.app.state.server_manager.server_info.get(name)
    if (
        not re.fullmatch(r"[\w.-]+", name)
        or config is None
        or info is None
        or info.get("gateway_component")
    ):
        raise HTTPException(404, "MCP 来源不存在")
    return config, info


def app_sources(request: Request) -> list[dict]:
    """轻量来源索引只返回网关地址与状态，不访问任何上游。"""
    _key(request)
    base = app_public_base_url(request)
    result = []
    for name in sorted(request.app.state.server_manager.server_info):
        try:
            config, info = _source(request, name)
        except HTTPException:
            continue
        result.append(
            {
                "server_name": name,
                "status": (
                    "running"
                    if config.get("enabled", True) and info.get("status") == "running"
                    else "unavailable"
                ),
                "endpoint_url": f"{base}/mcp/{quote(name, safe='')}" if base else None,
                "require_auth": config.get("require_auth", True),
                "auth_header_name": security_service.get_auth_header_name(),
            }
        )
    return result


def _client(request: Request, name: str, key: str) -> Client:
    """当前网关 Key 仅发送到本实例，不使用请求 Host 或上游 URL。"""
    port = getattr(request.app.state, "port", 8000)
    return Client(
        StreamableHttpTransport(f"http://127.0.0.1:{port}/mcp/{name}"),
        auth=McpcatAuth(key),
        timeout=25,
        extensions=[
            advertise(
                "io.modelcontextprotocol/ui",
                {"mimeTypes": ["text/html;profile=mcp-app"]},
            )
        ],
    )


async def _pages(client: Client) -> dict:
    """有界遍历完整工具页；过滤非应用，且不按跨来源名称去重。"""
    items: dict[tuple[str, str], dict] = {}
    cursor = None
    seen: set[str] = set()
    for _ in range(20):
        page = await listing(client, "apps", cursor)
        for tool in page["items"]:
            uri = app_uri(tool)
            item = {
                "name": tool["name"],
                "display_name": tool.get("title") or tool["name"],
                "description": tool.get("description") or "",
                "resource_uri": uri,
            }
            items[(item["name"], uri)] = item
            if len(items) > 1000:
                raise ValueError("应用目录超限")
        if (
            len(json.dumps(list(items.values()), ensure_ascii=False).encode())
            > 2 * 1024 * 1024
        ):
            raise ValueError("应用目录超限")
        cursor = page.get("nextCursor")
        if not cursor:
            return {
                "items": list(items.values()),
                "supported": page["supported"],
                "complete": True,
            }
        if cursor in seen:
            raise ValueError("来源返回重复游标")
        seen.add(cursor)
    return {"items": list(items.values()), "supported": True, "complete": False}


async def discover_source_apps(request: Request, name: str) -> dict:
    """独立来源的失败不改变其他来源结果，结束后拒绝失效身份与代际。"""
    key = _key(request)
    config, info = _source(request, name)
    if not config.get("enabled", True) or info.get("status") != "running":
        raise HTTPException(409, "MCP 来源未运行，无法发现应用")
    before, server = deepcopy(config), info.get("mcp")
    try:
        with anyio.fail_after(30):
            async with _operations:
                client = _client(request, name, key)
                try:
                    async with client:
                        result = await _pages(client)
                finally:
                    with anyio.move_on_after(3, shield=True):
                        await client.close()
    except TimeoutError:
        raise HTTPException(504, "来源应用发现超时，可单独重试") from None
    except Exception:
        _key(request)
        raise HTTPException(
            502, "来源应用目录暂不可读取，请检查服务状态或上游授权"
        ) from None
    _key(request)
    current, current_info = _source(request, name)
    if (
        current != before
        or current_info is not info
        or current_info.get("mcp") is not server
        or current_info.get("status") != "running"
    ):
        raise HTTPException(409, "来源在发现期间已改变，请重新刷新")
    return {"server_name": name, **result}
