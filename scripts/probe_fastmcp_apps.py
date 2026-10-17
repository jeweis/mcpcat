"""固定版本 Apps 的 JSON/renderer/上游代理原型，不执行上传代码。"""

import asyncio
import hashlib
import json
from importlib.metadata import version

from fastmcp import Client, FastMCP
from fastmcp.server import create_proxy
from prefab_ui.app import PrefabApp


async def probe(target: FastMCP) -> dict:
    """验证打开工具保留 Prefab 定义和可读取的官方 renderer。"""
    async with Client(target) as client:
        tools = await client.list_tools()
        tool = next(tool for tool in tools if tool.name == "open_dashboard")
        renderer_uri = tool.meta["ui"]["resourceUri"]
        opened = await client.call_tool("open_dashboard", {})
        assert opened.structured_content is not None
        renderer = await client.read_resource(renderer_uri)
        content = renderer[0].text.encode()
        assert content
        called = await client.call_tool("get_count", {})
        assert called.data == 7
        return {
            "renderer_uri": renderer_uri,
            "renderer_size": len(content),
            "renderer_sha256": hashlib.sha256(content).hexdigest(),
            "renderer_excerpt": renderer[0].text[:300],
            "has_ui_data": bool(opened.structured_content),
            "callback": called.data,
        }


async def main() -> None:
    """将 JSON 导入平台可信工具，再验证直接使用和公开代理。"""
    server = FastMCP("Apps probe")
    wire = {"view": {"type": "Heading", "content": "Dashboard"}, "state": {"count": 7}}

    @server.tool(app=True)
    def open_dashboard() -> PrefabApp:
        """Open a counter dashboard."""
        return PrefabApp.from_json(wire)

    @server.tool()
    def get_count() -> int:
        """Get current count."""
        return 7

    print(
        json.dumps(
            {
                "prefab": version("prefab-ui"),
                "direct": await probe(server),
                "proxy": await probe(create_proxy(server)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
