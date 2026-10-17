"""独立官方 SDK Host 夹具；所有业务请求走正式 MCP，不走预览管理 API。"""

import asyncio
import io
import re
import zipfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from app.models.app_bindings import AppToolBinding
from app.services.app_preview_cancellation import run_preview_request
from app.services.app_publication_service import publish_app_version
from app.services.app_registry_service import upload_app
from app.services.app_tool_bindings import (
    AppToolResolver,
    publication_validator,
    replace_app_bindings,
)
from app.storage.database import Database

HOST = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Official SDK reference Host</title>
<h1>官方 SDK 参考 Host · 真实 MCP</h1><label>夹具 <select id="variant"><option value="reference-app">官方 SDK HTML</option><option value="real-demo">Prefab 动态回调</option><option value="source-a:html">来源 A HTML</option><option value="source-b:html">来源 B HTML</option><option value="source-a:prefab">来源 A Prefab</option><option value="source-b:prefab">来源 B Prefab</option></select></label><button id="open">连接并打开 App</button><button id="close">关闭 Host</button>
<p id="status">未连接；不发送真实工具调用</p><section id="confirm" hidden><h2>确认真实工具调用</h2><pre></pre>
<button id="approve">批准本次夹具调用</button><button id="deny">拒绝本次调用</button></section>
<div id="container" style="width:900px"></div><script type="module" src="/ui/reference-host.js"></script></html>"""


async def seed_reference_app(database: Database, manager: Any, bundles: Path) -> None:
    """上传可信固定 SDK 客户端，初始化数据到达后自行请求绑定工具。"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "index.html",
            '<!doctype html><html><meta charset="utf-8"><body><h1>Reference App</h1><script src="client.js"></script></body></html>',
        )
        archive.writestr("client.js", (bundles / "reference-app.js").read_bytes())
    upload_app(
        database,
        slug="reference-app",
        display_name="Official SDK reference",
        version="1.0.0",
        content=output.getvalue(),
        filename="reference.zip",
    )
    resolver = AppToolResolver(database, manager, lambda: True)
    await replace_app_bindings(
        database,
        resolver,
        slug="reference-app",
        version="1.0.0",
        bindings=[
            AppToolBinding(
                alias="store", server_name="preview-fixture", tool_name="store"
            )
        ],
    )
    checked = await publication_validator(database, resolver, "reference-app", "1.0.0")
    publish_app_version(
        database,
        slug="reference-app",
        version="1.0.0",
        reviewed=True,
        validate_bindings=checked,
    )


def mcp_client(slug: str) -> Client:
    """固定测试回环地址；不信任 HTTP Host 头来决定凭证发送目标。"""
    endpoint = "mcpcat" if ":" in slug else "apps"
    return Client(
        StreamableHttpTransport(
            f"http://127.0.0.1:18765/mcp/{endpoint}",
            headers={"Mcpcat-Key": "apps-browser-fixture-read"},
        ),
        mode="auto",
    )


async def opened(client: Client, slug: str) -> tuple[Any, Any]:
    """从真实 tools/list 定位固定夹具，不伪造 UI 资源或动态结果。"""
    tools = await client.list_tools()
    if ":" in slug:
        source, kind = slug.split(":")
        tool = next(item for item in tools if item.name == f"{source}__open_{kind}")
        return tool, await client.call_tool(tool.name, {})
    tool = next(
        item
        for item in tools
        if item.description.startswith(f"Open App {slug}, fixed version 1.0.0.")
    )
    return tool, await client.call_tool(tool.name, {})


def tool_names(value: Any) -> set[str]:
    """只从真实初始数据读取已装配的工具引用，不猜测回调名称。"""
    if isinstance(value, dict):
        own = set(value.get("toolBindings", {}).values())
        if value.get("action") == "toolCall":
            own.add(value["tool"])
        return own.union(*(tool_names(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(tool_names(item) for item in value))
    return set()


class ReferenceRoutes:
    """独立 Host 测试路由，不进入生产 API，Key 只留在 MCP 客户端。"""

    def __init__(self, bundles: Path) -> None:
        self.bundles = bundles

    async def host(self) -> HTMLResponse:
        """返回测试 Host，不是生产管理菜单。"""
        return HTMLResponse(HOST)

    async def script(self) -> Response:
        """固定官方 SDK 的编译后测试脚本。"""
        return Response(
            (self.bundles / "reference-host.js").read_bytes(),
            media_type="text/javascript",
        )

    async def open_app(self, request: Request) -> dict:
        """通过真实回环 MCP 取得打开结果和资源字节。"""
        params = await request.json()
        slug = self.slug(params)
        async with mcp_client(slug) as client:
            return await self.view_data(client, slug)

    async def view_data(self, client: Client, slug: str) -> dict:
        """读取原始资源和 metadata；只提取提供给 Host 的当前回调名称。"""
        tool, result = await opened(client, slug)
        resource = await client.read_resource(tool.meta["ui"]["resourceUri"])
        remote = ":" in slug
        names = (
            set(re.findall(r"catapp_[0-9a-f]{48}", resource[0].text))
            if remote
            else tool_names(result.structured_content)
        )
        metadata = resource[0].meta or {}
        return {
            "html": resource[0].text,
            "initial_result": result.structured_content,
            "allowed_tools": sorted(names),
            "remote_source": remote,
            "csp": metadata.get("ui", {}).get("csp", {}),
        }

    def slug(self, params: dict) -> str:
        """只接受预先注册的可信夹具，不开放任意 MCP 转发。"""
        value = params.get("slug")
        if value not in {
            "reference-app",
            "real-demo",
            "source-a:html",
            "source-b:html",
            "source-a:prefab",
            "source-b:prefab",
        }:
            raise HTTPException(400, "Unknown fixture")
        return value

    async def call(self, request: Request) -> dict:
        """父页断连时取消实际 MCP 请求，不遗留慢调用。"""
        params = await request.json()
        slug = self.slug(params)
        if params.get("arguments") != (
            {"increment": 1} if ":" in slug else {"value": 7}
        ):
            raise HTTPException(400, "Unknown fixture arguments")
        return await run_preview_request(request, lambda: self.dispatch(slug, params))

    async def dispatch(self, slug: str, params: dict) -> dict:
        """只转交本夹具绑定；正式 MCP 的逐次授权仍会执行。"""
        async with mcp_client(slug) as client:
            data = await self.view_data(client, slug)
            if params.get("name") not in data["allowed_tools"]:
                raise HTTPException(403, "Outside fixture binding")
            result = await client.call_tool(params["name"], params["arguments"])
            return {
                "content": [
                    item.model_dump(by_alias=True, exclude_none=True)
                    for item in result.content
                ],
                "structuredContent": result.structured_content,
                "isError": result.is_error,
            }


def attach_reference_host(
    application: FastAPI, database: Database, manager: Any, bundles: Path
) -> None:
    """仅接入本机临时应用，独立父页确认不暴露凭证给 iframe。"""
    asyncio.run(seed_reference_app(database, manager, bundles))
    routes = ReferenceRoutes(bundles)
    for path, endpoint, method in (
        ("/ui/reference-host", routes.host, "GET"),
        ("/ui/reference-host.js", routes.script, "GET"),
        ("/ui/reference-api/open", routes.open_app, "POST"),
        ("/ui/reference-api/call", routes.call, "POST"),
    ):
        application.add_api_route(path, endpoint, methods=[method])
    added = [
        route
        for route in application.router.routes
        if getattr(route, "path", "").startswith("/ui/reference-")
    ]
    for route in added:
        application.router.routes.remove(route)
    application.router.routes[:0] = added
