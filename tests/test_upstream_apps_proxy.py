"""原生代理的 Apps 完整性：真实 HTTP 协议经 ASGI，不重写上游 HTML。"""

import base64
from contextlib import AsyncExitStack

import httpx2
import pytest
from fastmcp import Client, FastMCP
from fastmcp.apps import UI_MIME_TYPE, AppConfig
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.resources import ResourceContent, ResourceResult
from fastmcp.tools import ToolResult
from prefab_ui import PrefabApp

from app.services import proxy_clients
from app.services.mcp_factory import MCPServerFactory

URI = "ui://shared/dashboard.html"
ASSET = "asset://shared/payload"
POLICY = {"resourceDomains": ["https://assets.example.test"]}


def source_app(name: str) -> tuple[FastMCP, dict]:
    """两来源使用完全相同工具/资源名，动态状态必须独立。"""
    server = FastMCP(name)
    state = {"count": 0}

    @server.tool(app=AppConfig(resourceUri=URI), meta={"fixture/tool": name})
    def open_html() -> ToolResult:
        return ToolResult(
            content=f"HTML {name}: {state['count']}",
            structured_content={"source": name, "count": state["count"]},
            meta={"fixture/result": name},
        )

    @server.tool(app=True, meta={"fixture/tool": name})
    def open_prefab() -> PrefabApp:
        return PrefabApp.from_json(prefab_wire(name, state["count"]))

    @server.tool(app=AppConfig(visibility=["app"]))
    def save(increment: int) -> dict:
        state["count"] += increment
        return {"source": name, "count": state["count"]}

    add_resources(server, name)
    return server, state


def prefab_wire(name: str, count: int) -> dict:
    """官方 Prefab 数据和声明式回调，不执行上传后端。"""
    return {
        "state": {"source": name, "count": count},
        "view": {
            "type": "Column",
            "children": [
                {"type": "Text", "content": "{{ source }}: {{ count }}"},
                {
                    "type": "Button",
                    "label": "Save",
                    "onClick": {
                        "action": "toolCall",
                        "tool": "save",
                        "arguments": {"increment": 1},
                    },
                },
            ],
        },
    }


def add_resources(server: FastMCP, name: str) -> None:
    """MIME、CSP、权限和未知 metadata 均为原来源定义，不由网关扩大。"""
    metadata = {
        "ui": {"csp": POLICY, "permissions": {"clipboardWrite": {}}},
        "fixture/resource": name,
    }

    @server.resource(URI, mime_type=UI_MIME_TYPE, meta=metadata)
    def html() -> ResourceResult:
        return ResourceResult(
            [
                ResourceContent(
                    f"<!doctype html><p>{name}</p>",
                    mime_type=UI_MIME_TYPE,
                    meta=metadata,
                )
            ],
            meta={"fixture/read": name},
        )

    @server.resource(
        ASSET, mime_type="application/x-fixture", meta={"fixture/asset": name}
    )
    def asset() -> ResourceResult:
        return ResourceResult(
            [
                ResourceContent(
                    b"\x00\xff" + name.encode(),
                    mime_type="application/x-fixture",
                    meta={"fixture/file": name},
                )
            ]
        )


def proxy_targets(monkeypatch, applications: dict, headers: dict | None = None) -> dict:
    """仅替换公开 client factory 的网络承载，保留实际工厂/代理。"""

    def transport(**kwargs):
        application = applications[kwargs["url"]]
        return StreamableHttpTransport(
            **kwargs,
            httpx_client_factory=lambda **options: httpx2.AsyncClient(
                **options, transport=httpx2.ASGITransport(app=application)
            ),
        )

    monkeypatch.setattr(proxy_clients, "StreamableHttpTransport", transport)
    return {
        url: MCPServerFactory.create_server(
            url,
            {
                "type": "streamable-http",
                "url": url,
                "timeout": 5,
                "headers": headers or {},
            },
        )
        for url in applications
    }


async def snapshot(client: Client, opening: str) -> dict:
    """使用公开完整 result API，不能只比较渲染文本。"""
    tools = await client.list_tools()
    resources = await client.list_resources()
    opened = next(tool for tool in tools if tool.name == opening)
    callback = next(tool for tool in tools if tool.name == "save")
    uri = opened.meta["ui"]["resourceUri"]
    result = await client.call_tool_mcp(opening, {})
    resource = await client.read_resource_mcp(uri)
    assets = await client.read_resource_mcp(ASSET)
    return {
        "open_metadata": opened.meta,
        "callback_metadata": callback.meta,
        "tools": [
            tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            for tool in tools
        ],
        "resources": [
            resource.model_dump(mode="json", by_alias=True, exclude_none=True)
            for resource in resources
        ],
        "result": result.model_dump(mode="json", by_alias=True, exclude_none=True),
        "resource": resource.model_dump(mode="json", by_alias=True, exclude_none=True),
        "asset": assets.model_dump(mode="json", by_alias=True, exclude_none=True),
    }


def compare_snapshot(actual: dict, expected: dict, source: str, mode: str) -> None:
    """单独核对框架服务器身份语义；其余应用内容不作归一化。"""
    for field in ("result", "resource", "asset"):
        key = "io.modelcontextprotocol/serverInfo"
        received = actual[field].get("_meta", {}).pop(key, None)
        original = expected[field].get("_meta", {}).pop(key, None)
        assert original == (
            {"name": source, "version": "4.1.0"} if mode == "auto" else None
        )
        expected_server = source if field == "result" else "Config-Based Proxy"
        assert received == (
            {"name": expected_server, "version": "4.1.0"}
            if mode == "auto" or field == "result"
            else None
        )
        if (
            received is not None
            and not actual[field]["_meta"]
            and "_meta" not in expected[field]
        ):
            actual[field].pop("_meta")
    assert actual == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("opening", ["open_html", "open_prefab"])
async def test_factory_preserves_upstream_apps_and_same_name_routing(
    monkeypatch, mode, opening
):
    sources = {name: source_app(name) for name in ("alpha", "beta")}
    applications = {
        f"http://{name}.fixture/mcp": pair[0].http_app(path="/mcp", json_response=True)
        for name, pair in sources.items()
    }
    proxies = proxy_targets(monkeypatch, applications)
    async with AsyncExitStack() as stack:
        for application in applications.values():
            await stack.enter_async_context(application.lifespan(application))
        for name, (source, state) in sources.items():
            async with Client(source, mode=mode) as direct, Client(
                proxies[f"http://{name}.fixture/mcp"],
                mode=mode,
            ) as proxied:
                protocol = "2026-07-28" if mode == "auto" else "2025-11-25"
                assert direct.protocol_version == proxied.protocol_version == protocol
                expected = await snapshot(direct, opening)
                actual = await snapshot(proxied, opening)
                compare_snapshot(actual, expected, name, mode)
                assert actual["callback_metadata"]["ui"]["visibility"] == ["app"]
                assert actual["resource"]["contents"][0]["mimeType"] == UI_MIME_TYPE
                assert (
                    actual["asset"]["contents"][0]["blob"]
                    == base64.b64encode(b"\x00\xff" + name.encode()).decode()
                )
                saved = await proxied.call_tool("save", {"increment": 1})
                assert saved.data == {"source": name, "count": 1}
                assert state["count"] == 1
                other = "beta" if name == "alpha" else "alpha"
                assert sources[other][1]["count"] == (0 if name == "alpha" else 1)
                changed = await snapshot(proxied, opening)
                assert (
                    changed["result"]["structuredContent"]
                    != actual["result"]["structuredContent"]
                )
