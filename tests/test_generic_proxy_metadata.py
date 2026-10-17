"""普通网关组件不能因 Apps/Skills 适配丢失模板、提示或扩展 metadata。"""

from contextlib import AsyncExitStack

import pytest
from fastmcp import Client, FastMCP
from fastmcp.prompts import Message, PromptResult
from fastmcp.resources import ResourceContent, ResourceResult
from fastmcp.tools import ToolResult
from mcp_types import Icon
from pydantic import BaseModel
from test_upstream_apps_proxy import proxy_targets

ICON = Icon(src="data:image/png;base64,AA==", mimeType="image/png", sizes=["24x24"])
TEMPLATE = "fixture://shared/items/{key}"


def generic_source(name: str) -> FastMCP:
    """同名组件带来源 metadata，包含不能规范化的 CRLF/Unicode 内容。"""
    source = FastMCP(name)

    @source.tool(
        icons=[ICON], annotations={"readOnlyHint": True}, meta={"fixture/tool": name}
    )
    def inspect_item(key: str) -> ToolResult:
        return ToolResult(
            content=f"{name}: {key}",
            structured_content={"source": name},
            meta={"fixture/result": {"source": name, "nested": [1, True]}},
        )

    @source.prompt(icons=[ICON], meta={"fixture/prompt": name})
    def explain(key: str) -> PromptResult:
        return PromptResult(
            [Message(f"{name}: {key}\r\n中文 😀")],
            description=f"Prompt from {name}",
            meta={"fixture/prompt-result": name},
        )

    @source.resource(
        TEMPLATE,
        mime_type="text/x-fixture",
        icons=[ICON],
        annotations={"audience": ["assistant"], "priority": 0.5},
        meta={"fixture/template": name},
    )
    def item(key: str) -> ResourceResult:
        return ResourceResult(
            [
                ResourceContent(
                    f"{name}: {key}\r\n中文 😀",
                    mime_type="text/x-fixture",
                    meta={"fixture/content": name},
                )
            ],
            meta={"fixture/read": name},
        )

    return source


async def generic_snapshot(client: Client) -> dict:
    """公开完整 wire 模型比较，不以纯文本 REST 快照替代。"""

    def convert(value: BaseModel) -> dict:
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)

    return {
        "tools": [convert(value) for value in await client.list_tools()],
        "prompts": [convert(value) for value in await client.list_prompts()],
        "templates": [
            convert(value) for value in await client.list_resource_templates()
        ],
        "tool_result": convert(
            await client.call_tool_mcp("inspect_item", {"key": "first"})
        ),
        "prompt_result": convert(
            await client.get_prompt_mcp("explain", {"key": "first"})
        ),
        "resource_result": convert(
            await client.read_resource_mcp("fixture://shared/items/first")
        ),
    }


def compare_results(actual: dict, expected: dict, source: str, mode: str) -> None:
    """框架标准服务器身份独立断言，其余每个字段完整保留。"""
    for field in ("tool_result", "prompt_result", "resource_result"):
        name = "io.modelcontextprotocol/serverInfo"
        actual_info = actual[field].get("_meta", {}).pop(name, None)
        expected_info = expected[field].get("_meta", {}).pop(name, None)
        assert expected_info == (
            {"name": source, "version": "4.1.0"} if mode == "auto" else None
        )
        expected_server = "Config-Based Proxy" if field == "resource_result" else source
        assert actual_info == (
            {"name": expected_server, "version": "4.1.0"}
            if mode == "auto" or field != "resource_result"
            else None
        )
    assert actual == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_prompts_templates_icons_and_unknown_metadata_preserved(
    monkeypatch, mode
):
    sources = {name: generic_source(name) for name in ("alpha", "beta")}
    applications = {
        f"http://{name}.fixture/mcp": source.http_app(path="/mcp", json_response=True)
        for name, source in sources.items()
    }
    proxies = proxy_targets(monkeypatch, applications)
    async with AsyncExitStack() as stack:
        for application in applications.values():
            await stack.enter_async_context(application.lifespan(application))
        for name, source in sources.items():
            async with Client(source, mode=mode) as direct, Client(
                proxies[f"http://{name}.fixture/mcp"],
                mode=mode,
            ) as proxied:
                expected = await generic_snapshot(direct)
                actual = await generic_snapshot(proxied)
                compare_results(actual, expected, name, mode)
                assert (
                    actual["resource_result"]["contents"][0]["text"]
                    == f"{name}: first\r\n中文 😀"
                )
                assert actual["templates"][0]["uriTemplate"] == TEMPLATE
                assert actual["tools"][0]["icons"][0]["sizes"] == ["24x24"]
                if mode == "auto":
                    assert proxied.protocol_version == "2026-07-28"
                    assert (
                        proxied.server_capabilities.extensions[
                            "io.modelcontextprotocol/ui"
                        ]
                        == {}
                    )
                else:
                    assert proxied.protocol_version == "2025-11-25"
