"""分页、各种 MCP 正文与非法模板的公开协议验收。"""

import base64

import pytest
from fastmcp import Client, FastMCP
from fastmcp.prompts import Message, PromptResult
from fastmcp.resources import ResourceContent, ResourceResult
from fastmcp.tools import ToolResult
from mcp import types
from tests.test_upstream_apps_proxy import proxy_targets

from app.services import inspector_workbench as workbench


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_pagination_rich_content_and_error_boundaries(monkeypatch, mode):
    source = FastMCP("rich source", list_page_size=2)

    def reader(number):
        def read():
            return str(number)

        return read

    for number in range(5):
        source.resource(f"fixture://page/{number}")(reader(number))
        source.prompt(name=f"page_{number}")(reader(number))
        source.tool(name=f"page_{number}")(reader(number))

    def template(name: str) -> str:
        return name

    for number in range(5):
        source.resource(f"fixture://template/{number}/{{name}}")(template)

    @source.resource("fixture://binary", mime_type="application/octet-stream")
    def binary() -> ResourceResult:
        return ResourceResult(
            [ResourceContent(b"\x00\xff\r\n", mime_type="application/octet-stream")]
        )

    blocks = [
        types.TextContent(type="text", text="\r\n中文 😀"),
        types.ImageContent(type="image", data="AA==", mime_type="image/png"),
        types.AudioContent(type="audio", data="AA==", mime_type="audio/wav"),
        types.EmbeddedResource(
            type="resource",
            resource=types.TextResourceContents(
                uri="fixture://embedded", mime_type="text/markdown", text="# Embedded"
            ),
        ),
        types.EmbeddedResource(
            type="resource",
            resource=types.BlobResourceContents(
                uri="fixture://blob", mime_type="application/octet-stream", blob="AP8="
            ),
        ),
        types.ResourceLink(
            type="resource_link", name="reference", uri="fixture://binary"
        ),
    ]

    @source.prompt()
    def rich() -> PromptResult:
        return PromptResult(
            [
                Message(block, role="user" if index % 2 else "assistant")
                for index, block in enumerate(blocks[:5])
            ]
        )

    @source.tool()
    def result() -> ToolResult:
        return ToolResult(
            content=blocks,
            structured_content={"array": [True, None]},
            meta={"fixture/unknown": {"retained": True}},
        )

    app = source.http_app(path="/mcp", json_response=True)
    url = "http://rich.fixture/mcp"
    gateway = proxy_targets(monkeypatch, {url: app})[url]
    paged = FastMCP("paged gateway", list_page_size=2)
    paged.mount(gateway)
    async with app.lifespan(app), Client(paged, mode=mode) as client:
        for kind, field, count in [
            ("resources", "uri", 6),
            ("templates", "uriTemplate", 5),
            ("prompts", "name", 6),
            ("tools", "name", 6),
        ]:
            names, cursor, pages = [], None, 0
            while True:
                if kind == "tools":
                    raw = workbench.dump(await client.list_tools_mcp(cursor=cursor))
                    page = {"items": raw["tools"], "nextCursor": raw.get("nextCursor")}
                else:
                    page = await workbench.listing(client, kind, cursor)
                names.extend(item[field] for item in page["items"])
                pages += 1
                cursor = page.get("nextCursor")
                if cursor is None:
                    break
            assert len(names) == len(set(names)) == count
            assert pages >= 3
        binary_result = await workbench.read_resource(client, "fixture://binary")
        assert base64.b64decode(binary_result["contents"][0]["blob"]) == b"\x00\xff\r\n"
        prompt = await workbench.get_prompt(client, "rich", {})
        assert [message["content"] for message in prompt["messages"]] == [
            block.model_dump(mode="json", by_alias=True, exclude_none=True)
            for block in blocks[:5]
        ]
        response = await client.call_tool_mcp("result", {})
        assert [block.type for block in response.content] == [
            block.type for block in blocks
        ]
        assert response.structured_content == {"array": [True, None]}
        assert response.meta["fixture/unknown"] == {"retained": True}
        with pytest.raises(Exception):
            await workbench.read_resource(client, "fixture://missing")
        with pytest.raises(Exception):
            await workbench.get_prompt(client, "missing", {})
        with pytest.raises(Exception):
            await workbench.listing(client, "resources", "not-a-valid-cursor")
