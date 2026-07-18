"""Catalog Provider 与官方 Search Transform 集成测试。"""

from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP

from app.models.mcp_config import CatalogConfig
from app.services.catalog_provider import CatalogProvider, make_namespaced_name
from app.services.catalog_search import _chinese_ngrams
from app.services.catalog_service import CatalogService
from app.services.server_manager import is_reserved_name


def make_server(description: str, result_prefix: str) -> FastMCP:
    """创建带固定 lookup 工具的测试 MCP 服务。"""
    server = FastMCP(result_prefix)

    async def lookup(topic: str) -> str:
        return f"{result_prefix}:{topic}"

    lookup.__name__ = "lookup"
    lookup.__doc__ = description
    server.add_tool(lookup)
    return server


def server_info(
    server: FastMCP,
    *,
    running: bool = True,
    exposed: bool = True,
    oauth=None,
    note: str = "",
    tags=None,
) -> dict:
    """创建最小 manager server_info 条目。"""
    return {
        "mcp": server,
        "status": "running" if running else "stopped",
        "config": {
            "expose_in_catalog": exposed,
            "oauth": oauth,
            "note": note,
            "tags": tags or [],
        },
    }


class TestReservedNames:
    def test_reserved_names(self):
        assert is_reserved_name("mcpcat") is True
        assert is_reserved_name("_anything") is True
        assert is_reserved_name("fetch") is False


class TestCatalogProvider:
    @pytest.mark.asyncio
    async def test_filters_and_enriches_tools(self):
        manager = SimpleNamespace(
            server_info={
                "visible": server_info(
                    make_server("Find weather forecasts.", "visible"),
                    note="Read-only weather tools",
                    tags=["forecast"],
                ),
                "hidden": server_info(
                    make_server("Hidden tool.", "hidden"),
                    exposed=False,
                ),
                "stopped": server_info(
                    make_server("Stopped tool.", "stopped"),
                    running=False,
                ),
                "oauth": server_info(
                    make_server("OAuth tool.", "oauth"),
                    oauth={"token": {"access_token": "secret"}},
                ),
            }
        )
        provider = CatalogProvider(manager, CatalogConfig())

        tools = await provider.list_tools()

        assert [tool.name for tool in tools] == ["visible__lookup"]
        assert "MCP service: visible" in tools[0].description
        assert "Read-only weather tools" in tools[0].description
        assert "forecast" in tools[0].description

    @pytest.mark.asyncio
    async def test_routes_server_names_containing_separator(self):
        manager = SimpleNamespace(
            server_info={
                "my": server_info(make_server("Short service.", "short")),
                "my__server": server_info(make_server("Long service.", "long")),
            }
        )
        provider = CatalogProvider(manager, CatalogConfig())

        tool = await provider.get_tool("my__server__lookup")

        assert tool is not None
        assert tool.name == "my__server__lookup"
        result = await tool.run({"topic": "value"})
        assert result.content[0].text == "long:value"

    @pytest.mark.asyncio
    async def test_rejects_real_namespace_collision(self):
        short = FastMCP("short")
        long = FastMCP("long")

        @short.tool(name="b__lookup")
        def short_tool() -> str:
            return "short"

        @long.tool(name="lookup")
        def long_tool() -> str:
            return "long"

        manager = SimpleNamespace(
            server_info={
                "a": server_info(short),
                "a__b": server_info(long),
            }
        )
        provider = CatalogProvider(manager, CatalogConfig())

        tools = await provider.list_tools()
        tool = await provider.get_tool("a__b__lookup")

        assert tools == []
        assert tool is None


class TestCatalogSearchTransform:
    def test_chinese_ngrams_are_literal_and_ignore_single_characters(self):
        ngrams = _chinese_ngrams("[顺序思考.*]")

        assert "顺序" in ngrams
        assert "顺序思考" in ngrams
        assert "顺" not in ngrams
        assert ".*" not in ngrams

    @pytest.mark.asyncio
    async def test_chinese_metadata_and_long_query_are_recalled(self):
        manager = SimpleNamespace(
            server_info={
                "thinking": server_info(
                    make_server("Reflect on complex problems.", "thinking"),
                    note="顺序思考 分步思考 复杂问题分析",
                ),
                "time": server_info(
                    make_server("Read a timezone clock.", "time"),
                    note="当前时间 世界时间 时区转换",
                ),
            }
        )
        service = CatalogService(manager, CatalogConfig(max_results=5))

        exact = await service.search_tools("顺序思考")
        long_query = await service.search_tools("请帮我查一下当前时间")

        assert [tool["name"] for tool in exact] == ["thinking__lookup"]
        assert [tool["name"] for tool in long_query] == ["time__lookup"]

    @pytest.mark.asyncio
    async def test_bm25_results_stay_before_chinese_supplements(self):
        manager = SimpleNamespace(
            server_info={
                "english": server_info(
                    make_server("Find weather forecasts.", "english")
                ),
                "chinese": server_info(
                    make_server("Unrelated helper.", "chinese"),
                    note="天气预报",
                ),
            }
        )
        service = CatalogService(manager, CatalogConfig(max_results=5))

        results = await service.search_tools("weather 天气预报")

        assert [tool["name"] for tool in results] == [
            "english__lookup",
            "chinese__lookup",
        ]

    @pytest.mark.asyncio
    async def test_chinese_scores_limit_results_without_filling_zero_matches(self):
        manager = SimpleNamespace(
            server_info={
                "exact": server_info(
                    make_server("Exact helper.", "exact"),
                    note="查询技术文档",
                ),
                "partial": server_info(
                    make_server("Partial helper.", "partial"),
                    note="查询资料",
                ),
                "other": server_info(
                    make_server("Other helper.", "other"),
                    note="发送邮件",
                ),
            }
        )
        service = CatalogService(manager, CatalogConfig(max_results=1))

        matched = await service.search_tools("帮我查询技术文档")
        unrelated = await service.search_tools("预订机票")

        assert [tool["name"] for tool in matched] == ["exact__lookup"]
        assert unrelated == []

    @pytest.mark.asyncio
    async def test_generic_chinese_action_word_does_not_create_false_positive(self):
        manager = SimpleNamespace(
            server_info={
                "docs": server_info(
                    make_server("Documentation helper.", "docs"),
                    note="查询技术文档",
                ),
                "time": server_info(
                    make_server("Timezone helper.", "time"),
                    note="时区查询 当前时间",
                ),
            }
        )
        service = CatalogService(manager, CatalogConfig(max_results=5))

        results = await service.search_tools("查询技术文档")

        assert [tool["name"] for tool in results] == ["docs__lookup"]

    @pytest.mark.asyncio
    async def test_dynamic_add_replace_remove_after_index_creation(self):
        manager = SimpleNamespace(
            server_info={
                "alpha": server_info(
                    make_server("Find alpha clock information.", "alpha")
                )
            }
        )
        service = CatalogService(manager, CatalogConfig(max_results=5))
        service._init_mcp()

        async with Client(service._mcp) as client:
            listed = await client.list_tools()
            assert [tool.name for tool in listed] == [
                "search_tools",
                "call_tool",
            ]

            initial = await client.call_tool(
                "search_tools",
                {"query": "alpha clock"},
            )
            assert initial.data[0]["name"] == "alpha__lookup"

            manager.server_info["beta"] = server_info(
                make_server("Find beta weather forecasts.", "beta")
            )
            added = await client.call_tool(
                "search_tools",
                {"query": "beta weather"},
            )
            assert added.data[0]["name"] == "beta__lookup"

            called = await client.call_tool(
                "call_tool",
                {
                    "name": "beta__lookup",
                    "arguments": {"topic": "rain"},
                },
            )
            assert called.data == "beta:rain"

            manager.server_info["beta"] = server_info(
                make_server("Find beta stock prices.", "beta2")
            )
            old_description = await client.call_tool(
                "search_tools", {"query": "weather forecasts"}
            )
            new_description = await client.call_tool(
                "search_tools", {"query": "stock prices"}
            )
            assert old_description.data == []
            assert new_description.data[0]["name"] == "beta__lookup"

            del manager.server_info["beta"]
            removed = await client.call_tool(
                "search_tools",
                {"query": "stock prices"},
            )
            assert removed.data == []

    @pytest.mark.asyncio
    async def test_service_rest_helpers_use_official_tools(self):
        manager = SimpleNamespace(
            server_info={
                "time": server_info(
                    make_server("Return current timezone clock.", "time")
                )
            }
        )
        service = CatalogService(manager, CatalogConfig())

        results = await service.search_tools("timezone clock")
        called = await service.call_tool("time__lookup", {"topic": "UTC"})
        await service.refresh_index()

        assert results[0]["name"] == make_namespaced_name("time", "lookup")
        assert called == "time:UTC"
        assert service.tool_count == 1
        assert service.last_refresh > 0

    @pytest.mark.asyncio
    async def test_unexposed_tool_cannot_be_called(self):
        manager = SimpleNamespace(
            server_info={
                "private": server_info(
                    make_server("Private operation.", "private"),
                    exposed=False,
                )
            }
        )
        service = CatalogService(manager, CatalogConfig())

        with pytest.raises(Exception):
            await service.call_tool("private__lookup", {"topic": "secret"})
