"""四类真实上游与 MCP/Inspector/Catalog；只使用临时 cwd 和本机端口。"""

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx2
from fastapi import FastAPI
from fastmcp import Client, FastMCP
from fastmcp.client.transports import SSETransport, StreamableHttpTransport

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.probe_fastmcp_network import serve, serve_app
from scripts.probe_proxy_network import mcporter_call


def rest_source() -> FastAPI:
    """实际 OpenAPI 文档和 HTTP 数据源，排除文档路由。"""
    app = FastAPI()

    @app.get("/echo", operation_id="echo")
    def echo(value: str) -> str:
        """Return input unchanged."""
        return value

    return app


async def configure_sources(stack: AsyncExitStack) -> dict[str, dict]:
    """可信 STDIO 子进程、HTTP/SSE MCP 与原生 OpenAPI，不替换工厂。"""
    source = FastMCP("Gateway matrix upstream")

    @source.tool()
    def echo(value: str) -> str:
        """Return input unchanged."""
        return value

    http = await stack.enter_async_context(serve(source, "http"))
    sse = await stack.enter_async_context(serve(source, "sse"))
    rest = await stack.enter_async_context(serve_app(rest_source()))
    return {
        "stdio": {
            "type": "stdio",
            "command": sys.executable,
            "args": [str(ROOT / "tests/fixtures/mcp_stdio_probe.py")],
            "env": {},
        },
        "http": {"type": "streamable-http", "url": http},
        "sse": {"type": "sse", "url": sse},
        "openapi": {
            "type": "openapi",
            "api_base_url": rest,
            "spec_url": rest + "/openapi.json",
            "route_configs": [{"methods": ["GET"], "pattern": "^/echo$"}],
        },
    }


async def check_mcp(root: str, name: str, mode: str, sse: bool) -> dict:
    """协议客户端从 manager 实际挂载的 HTTP/SSE 路由读取 Schema 并调用。"""
    target = (
        SSETransport(f"{root}/sse/{name}/")
        if sse
        else StreamableHttpTransport(f"{root}/mcp/{name}/")
    )
    async with Client(target, mode=mode, timeout=15) as client:
        assert client.protocol_version == (
            "2026-07-28" if mode == "auto" else "2025-11-25"
        )
        tools = await client.list_tools()
        assert len(tools) == 1
        tool = tools[0]
        assert "value" in tool.input_schema["properties"]
        result = await client.call_tool(tool.name, {"value": "passed"})
        assert result.data == "passed" and not result.is_error
        return {
            "upstream": name,
            "route": "sse" if sse else "http",
            "mode": mode,
            "protocol": client.protocol_version,
            "result": "passed",
        }


async def check_catalog(root: str, mode: str, names: list[str]) -> dict:
    """实际 MCP synthetic search/call，防止只验证 Provider 的 Python 调用。"""
    async with Client(f"{root}/mcp/mcpcat/", mode=mode, timeout=20) as client:
        assert client.protocol_version == (
            "2026-07-28" if mode == "auto" else "2025-11-25"
        )
        tools = await client.list_tools()
        assert {tool.name for tool in tools} == {"search_tools", "call_tool"}
        found = await client.call_tool("search_tools", {"query": "echo"})
        for name in names:
            selected = [t for t in found.data if t["name"].startswith(name + "__")]
            assert len(selected) == 1
            result = await client.call_tool(
                "call_tool",
                {"name": selected[0]["name"], "arguments": {"value": "passed"}},
            )
            # 官方 OpenAPI 将 primitive 放在 result 对象；synthetic call 没有原工具
            # outputSchema，因此保留该 structuredContent，不擅自解包或丢弃字段。
            expected = {"result": "passed"} if name == "openapi" else "passed"
            assert result.data == expected and not result.is_error, (name, mode, result)
    return {"route": "catalog-mcp", "mode": mode, "sources": names, "result": "passed"}


async def check_rest(root: str, names: list[str]) -> list[dict]:
    """真实 Inspector REST session/schema/result/close 与 Catalog REST。"""
    report = []
    async with httpx2.AsyncClient(base_url=root, timeout=30) as client:
        for name in names:
            response = await client.post(
                "/api/inspector/sessions", json={"server_name": name}
            )
            response.raise_for_status()
            path = "/api/inspector/sessions/" + response.json()["session_id"]
            try:
                tools = (await client.get(path + "/tools")).json()["tools"]
                assert len(tools) == 1
                assert "value" in tools[0]["input_schema"]["properties"]
                response = await client.post(
                    path + "/call",
                    json={"tool": tools[0]["name"], "arguments": {"value": "passed"}},
                )
                response.raise_for_status()
                assert response.json()["data"] == "passed"
                assert set(response.json()) == {"data", "content", "is_error"}
                report.append(
                    {"upstream": name, "route": "inspector-rest", "result": "passed"}
                )
            finally:
                (await client.delete(path)).raise_for_status()
        response = await client.get("/api/catalog/search", params={"query": "echo"})
        response.raise_for_status()
        tools = response.json()["tools"]
        for name in names:
            selected = [t for t in tools if t["name"].startswith(name + "__")]
            assert len(selected) == 1
            response = await client.post(
                "/api/catalog/call",
                json={"name": selected[0]["name"], "arguments": {"value": "passed"}},
            )
            response.raise_for_status()
            expected = {"result": "passed"} if name == "openapi" else "passed"
            assert response.json() == {"result": expected}
            report.append(
                {"upstream": name, "route": "catalog-rest", "result": "passed"}
            )
    return report


async def check_gateway(configs: dict[str, dict]) -> list[dict]:
    """所有应用模块只在临时 cwd 后导入，不接触用户配置和数据库。"""
    from app.api.catalog import router as catalog_router
    from app.api.inspector import router as inspector_router
    from app.models.mcp_config import CatalogConfig
    from app.services.catalog_service import CatalogService
    from app.services.inspector_service import inspector_service
    from app.services.server_manager import MCPServerManager

    manager = MCPServerManager()
    for name, config in configs.items():
        # OpenAPI 文档是同步 HTTP 下载：预加载放工作线程，让测试上游继续响应。
        assert await asyncio.to_thread(
            manager.add_mcp_server,
            name,
            {**config, "expose_in_catalog": True, "timeout": 15},
        )
    manager.catalog_service = CatalogService(manager, CatalogConfig(max_results=20))
    app = FastAPI(lifespan=manager.create_unified_lifespan)
    app.state.server_manager = manager
    app.include_router(inspector_router, prefix="/api/inspector")
    app.include_router(catalog_router, prefix="/api")
    manager.mount_all_servers(app)
    report = []
    async with serve_app(app) as root:
        app.state.port = int(root.rsplit(":", 1)[1])
        for name in configs:
            for mode, sse in (("auto", False), ("legacy", False), ("legacy", True)):
                report.append(await check_mcp(root, name, mode, sse))
            await mcporter_call(f"{root}/mcp/{name}/")
            report.append(
                {"upstream": name, "client": "mcporter@0.13.7", "result": "passed"}
            )
        for mode in ("auto", "legacy"):
            report.append(await check_catalog(root, mode, list(configs)))
        report.extend(await check_rest(root, list(configs)))
        assert not inspector_service.sessions
    assert not manager.lifecycle.handles and not manager.lifecycle.background
    assert not manager.lifecycle.requests and not manager.dynamic_tasks
    return report


async def main() -> None:
    """退出时清理进程/端口；仅新建临时数据库，不使用真实 API Key。"""
    previous = os.getcwd()
    with TemporaryDirectory(prefix="mcpcat-gateway-matrix-") as directory:
        os.chdir(directory)
        from app.core.config import settings
        from app.services.config_service import ConfigService
        from app.storage.database import Database
        from app.storage.migrations import upgrade_schema

        settings.mcpcat_backups_path = str(Path(directory) / "backups")
        database = Database(Path(directory) / "probe.db")
        try:
            upgrade_schema(database)
            ConfigService.configure_database(database)
            async with AsyncExitStack() as stack:
                configs = await configure_sources(stack)
                report = await check_gateway(configs)
                print(json.dumps(report, indent=2))
        finally:
            ConfigService.reset_database()
            database.dispose()
            os.chdir(previous)


if __name__ == "__main__":
    asyncio.run(main())
