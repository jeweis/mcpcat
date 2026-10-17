"""原始 Catalog 名称/Schema、缩小范围和每次动态过滤。"""

from types import SimpleNamespace

import pytest
from fastmcp import FastMCP
from fastmcp.tools import Tool
from test_catalog import make_server, server_info

from app.models.code_mode_config import CodeModeConfig
from app.models.mcp_config import CatalogConfig
from app.services.code_mode_provider import (
    GATEWAY_COMPONENT_META,
    ORIGIN_META,
    CodeModeProvider,
)


def provider(manager, config=None, authorize=lambda: True):
    state = {"config": config or CodeModeConfig(enabled=True)}
    directory = CodeModeProvider(
        manager,
        authorize=authorize,
        config_loader=lambda: state["config"],
        catalog_loader=CatalogConfig,
    )
    return directory, state


@pytest.mark.asyncio
async def test_raw_same_name_tools_schema_and_catalog_eligibility():
    manager = SimpleNamespace(
        server_info={
            "a": server_info(make_server("Find weather.", "A")),
            "b": server_info(make_server("Find history.", "B")),
            "hidden": server_info(make_server("Hidden.", "hidden"), exposed=False),
            "oauth": server_info(
                make_server("OAuth.", "oauth"), oauth={"enabled": True}
            ),
            "stopped": server_info(make_server("Stopped.", "stopped"), running=False),
        }
    )
    directory, state = provider(manager)
    tools = await directory.list_tools()
    assert {tool.name for tool in tools} == {"a__lookup", "b__lookup"}
    for tool in tools:
        assert "topic" in tool.parameters["properties"]
        assert tool.get_meta()[ORIGIN_META]["tool"] == "lookup"
    state["config"] = CodeModeConfig(
        enabled=True, allowed_services=["a"], allowed_tools=["b__lookup"]
    )
    assert await directory.list_tools() == []
    assert await directory.get_tool("b__lookup") is None
    state["config"] = CodeModeConfig(enabled=True, allowed_services=["a"])
    assert [tool.name for tool in await directory.list_tools()] == ["a__lookup"]
    state["config"] = CodeModeConfig(enabled=True, allowed_tools=[])
    assert await directory.list_tools() == []


@pytest.mark.asyncio
async def test_live_authorization_stop_disable_and_scope_change():
    manager = SimpleNamespace(server_info={"a": server_info(make_server("Live.", "A"))})
    identity = {"allowed": True}
    directory, state = provider(manager, authorize=lambda: identity["allowed"])
    assert await directory.get_tool("a__lookup") is not None
    identity["allowed"] = False
    assert await directory.list_tools() == []
    assert await directory.get_tool("a__lookup") is None
    identity["allowed"] = True
    manager.server_info["a"]["status"] = "stopped"
    assert await directory.get_tool("a__lookup") is None
    manager.server_info["a"]["status"] = "running"
    state["config"] = CodeModeConfig(enabled=False)
    assert await directory.get_tool("a__lookup") is not None
    state["config"] = CodeModeConfig(allowed_services=[])
    assert await directory.list_tools() == []


@pytest.mark.asyncio
async def test_apps_and_gateway_meta_excluded_but_ordinary_execute_preserved():
    mcp = FastMCP("upstream")
    for name, metadata in [
        ("execute", None),
        ("ui_only", {"ui": {"visibility": ["app"]}}),
        ("app_open", {"ui": {"resourceUri": "ui://demo"}}),
        ("code_meta", {GATEWAY_COMPONENT_META: "code-mode"}),
    ]:
        mcp.add_tool(Tool.from_function(lambda: "ok", name=name, meta=metadata))
    manager = SimpleNamespace(server_info={"upstream": server_info(mcp)})
    directory, _ = provider(manager)
    assert [tool.name for tool in await directory.list_tools()] == ["upstream__execute"]
    assert await directory.get_tool("upstream__ui_only") is None
    assert await directory.get_tool("upstream__code_meta") is None


@pytest.mark.asyncio
async def test_narrowing_does_not_reenable_catalog_alias_collision():
    a = FastMCP("a")
    a.add_tool(Tool.from_function(lambda: "a", name="b__lookup"))
    manager = SimpleNamespace(
        server_info={
            "a": server_info(a),
            "a__b": server_info(make_server("B.", "b")),
        }
    )
    directory, _ = provider(
        manager, CodeModeConfig(enabled=True, allowed_services=["a__b"])
    )
    assert await directory.list_tools() == []
    assert await directory.get_tool("a__b__lookup") is None


@pytest.mark.asyncio
async def test_authorization_revoked_while_querying_cannot_return_old_catalog(
    monkeypatch,
):
    manager = SimpleNamespace(server_info={"a": server_info(make_server("Live.", "A"))})
    identity = {"allowed": True}
    directory, _ = provider(manager, authorize=lambda: identity["allowed"])
    original = directory._list_server_tools

    async def revoked(server_name, info):
        tools = await original(server_name, info)
        identity["allowed"] = False
        return tools

    monkeypatch.setattr(directory, "_list_server_tools", revoked)
    assert await directory.list_tools() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    [
        CodeModeConfig(enabled=True, allowed_services=[]),
        CodeModeConfig(enabled=True, allowed_services=["other"]),
        CodeModeConfig(enabled=True, allowed_tools=["a__different"]),
    ],
)
async def test_hidden_lookup_rejected_before_upstream_description(monkeypatch, config):
    manager = SimpleNamespace(server_info={"a": server_info(make_server("Live.", "A"))})
    directory, _ = provider(manager, config)

    async def forbidden(*args, **kwargs):
        raise AssertionError("hidden lookup must not query upstream")

    monkeypatch.setattr(
        "app.services.catalog_provider.CatalogProvider._get_tool", forbidden
    )
    assert await directory.get_tool("a__lookup") is None
