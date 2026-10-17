"""Catalog 是模型发现/调用表面，不能借 Search wrapper 暴露 UI-only。"""

from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP
from fastmcp.tools import Tool
from test_catalog import server_info

from app.models.mcp_config import CatalogConfig
from app.services.catalog_provider import CatalogProvider
from app.services.catalog_service import CatalogService


def apps_source():
    source = FastMCP("apps")
    for name, meta in [
        ("ordinary", None),
        (
            "open",
            {
                "ui": {
                    "resourceUri": "ui://source/view.html",
                    "visibility": ["app", "model"],
                }
            },
        ),
        ("save", {"ui": {"visibility": ["app"]}}),
        ("none", {"ui": {"visibility": []}}),
    ]:
        source.add_tool(Tool.from_function(lambda: "done", name=name, meta=meta))
    return source


@pytest.mark.asyncio
async def test_catalog_keeps_app_open_metadata_but_denies_ui_only_guesses():
    source = apps_source()
    manager = SimpleNamespace(server_info={"source": server_info(source)})
    provider = CatalogProvider(manager, CatalogConfig())
    tools = await provider.list_tools()
    assert {tool.name for tool in tools} == {"source__ordinary", "source__open"}
    opened = await provider.get_tool("source__open")
    assert opened.meta["ui"]["resourceUri"] == "ui://source/view.html"
    assert await provider.get_tool("source__save") is None
    assert await provider.get_tool("source__none") is None
    # 原来源 MCP 仍可按自身授权提供 UI 回调，Catalog 过滤不是全局删除。
    async with Client(source) as client:
        assert "save" in {tool.name for tool in await client.list_tools()}
        assert (await client.call_tool("save", {})).content[0].text == "done"


@pytest.mark.asyncio
async def test_hidden_alias_collision_does_not_reenable_another_source():
    first = FastMCP("a")
    first.add_tool(
        Tool.from_function(
            lambda: "hidden", name="b__save", meta={"ui": {"visibility": ["app"]}}
        )
    )
    second = FastMCP("a__b")
    second.add_tool(Tool.from_function(lambda: "other", name="save"))
    provider = CatalogProvider(
        SimpleNamespace(
            server_info={"a": server_info(first), "a__b": server_info(second)}
        ),
        CatalogConfig(),
    )
    assert await provider.list_tools() == []
    assert await provider.get_tool("a__b__save") is None


@pytest.mark.asyncio
async def test_real_search_transform_cannot_discover_or_call_app_only_tool():
    catalog = CatalogService(
        SimpleNamespace(server_info={"source": server_info(apps_source())}),
        CatalogConfig(),
    )
    results = await catalog.search_tools("save")
    assert "source__save" not in str(results)
    with pytest.raises(Exception):
        await catalog.call_tool("source__save", {})
    assert await catalog.call_tool("source__ordinary", {}) is not None
