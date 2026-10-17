"""两个同名资源/回调的可信上游 App，实际 Catalog HTTP 由参考 Host 连接。"""

from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.apps import UI_MIME_TYPE, AppConfig
from fastmcp.tools import ToolResult
from prefab_ui.renderer import get_renderer_csp, get_renderer_html

from app.models.mcp_config import CatalogConfig
from app.services.catalog_service import CatalogService
from app.services.config_service import ConfigService
from app.storage.runtime_identities import endpoint_identity


def make_source(name: str, bundles: Path, counts: dict) -> FastMCP:
    """固定源码定义服务，不启动上传后端或任何用户程序。"""
    server = FastMCP(name)
    counts[name] = 0
    html_uri, prefab_uri = "ui://shared/index.html", "ui://shared/prefab.html"

    @server.tool(app=AppConfig(resourceUri=html_uri))
    def open_html() -> ToolResult:
        """Open the source's trusted SDK HTML App."""
        return ToolResult(
            content=f"Source {name}",
            structured_content={"source": name, "count": counts[name]},
        )

    @server.resource(html_uri, mime_type=UI_MIME_TYPE, meta={"ui": {"csp": {}}})
    def html() -> str:
        """同 URI 的内容必须按来源路由，保持原脚本而非替换工具名。"""
        script = (
            (bundles / "reference-app.js").read_text().replace("</script", "<\\/script")
        )
        return f'<!doctype html><html><meta charset="utf-8"><body><h1>Source {name}</h1><script>{script}</script></body></html>'

    @server.tool(app=AppConfig(resourceUri=prefab_uri))
    def open_prefab() -> ToolResult:
        """Return unmodified Prefab dynamic state and original save action."""
        return ToolResult(
            content=f"Prefab {name}",
            structured_content=prefab_payload(name, counts[name]),
        )

    @server.resource(
        prefab_uri,
        mime_type=UI_MIME_TYPE,
        meta={"ui": {"csp": get_renderer_csp("bundled")}},
    )
    def prefab() -> str:
        """固定 bundled renderer，无外部 CDN 依赖。"""
        return get_renderer_html("bundled")

    @server.tool(app=AppConfig(visibility=["app"]))
    def save(increment: int) -> dict:
        """只增加本来源内存计数，不执行外部业务。"""
        counts[name] += increment
        return {"source": name, "count": counts[name]}

    return server


def prefab_payload(name: str, count: int) -> dict:
    """原始来源动作始终是 save；只有 JSON-RPC 中继负责来源路由。"""
    return {
        "state": {"source": name, "count": count},
        "view": {
            "type": "Column",
            "children": [
                {"type": "Heading", "content": "{{ source }}"},
                {"type": "Text", "content": "计数：{{ count }}"},
                {
                    "type": "Button",
                    "label": "保存到本来源",
                    "onClick": {
                        "action": "toolCall",
                        "tool": "save",
                        "arguments": {"increment": 1},
                        "onSuccess": {
                            "action": "setState",
                            "key": "count",
                            "value": "{{ $result.count }}",
                        },
                    },
                },
            ],
        },
    }


def attach_upstream_catalog(
    application: FastAPI, manager: Any, bundles: Path, counts: dict
) -> None:
    """为临时应用挂载正式 Catalog Provider 与正常 lifespan。"""
    source_counts = counts["sources"] = {}
    for name in ("source-a", "source-b"):
        server = make_source(name, bundles, source_counts)
        config = {
            "type": "stdio",
            "command": "fixture-never-executed",
            "require_auth": True,
            "expose_in_catalog": True,
        }
        assert ConfigService.add_server_to_config(name, config)
        endpoint_identity(manager.apps_runtime.database, "service", name)
        manager.server_info[name] = {
            "mcp": server,
            "config": config,
            "status": "running",
        }
    assert ConfigService.update_setting_section(
        "catalog", {"enabled": True, "require_auth": True}
    )
    manager.catalog_service = CatalogService(manager, CatalogConfig())
    http, sse = manager.catalog_service.create_apps()
    application.mount("/mcp/mcpcat", http)
    application.mount("/sse/mcpcat", sse)
    previous = manager.create_unified_lifespan

    @asynccontextmanager
    async def lifecycle(app: FastAPI) -> AsyncIterator[None]:
        """只管理新增 Catalog 生命周期，保留独立 Apps/Skills 原路径。"""
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(previous(app))
            await stack.enter_async_context(http.lifespan(app))
            await stack.enter_async_context(sse.lifespan(app))
            yield

    manager.create_unified_lifespan = lifecycle
