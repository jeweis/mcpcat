"""原生 MCP Apps 打开、固定回调命名和来源/发布授权不依赖 visibility。"""

import json
from dataclasses import replace

import pytest
from fastmcp import Client, FastMCP
from test_app_resources_provider import resources
from test_catalog import server_info
from test_skill_content_workflow import content_app

from app.models.app_bindings import AppToolBinding
from app.services.app_publication_service import (
    publish_app_version,
    withdraw_app_version,
)
from app.services.app_registry_service import upload_app
from app.services.app_resource_registry import app_callback_name
from app.services.app_tool_bindings import (
    AppToolResolver,
    publication_validator,
    replace_app_bindings,
)
from app.services.app_tools_provider import AppToolsProvider, app_open_name
from app.services.config_service import ConfigService


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_native_open_tool_links_renderer_and_preserves_state(resources, mode):
    database, _, registry, _, server, identity = resources
    resolver = AppToolResolver(database, object(), registry.authorize)
    server.add_provider(AppToolsProvider(registry, resolver))
    async with Client(server, mode=mode) as client:
        tool = next(
            item
            for item in await client.list_tools()
            if item.name == app_open_name(identity)
        )
        assert tool.meta["ui"]["resourceUri"] == identity.encode()
        result = await client.call_tool(tool.name, {})
        assert result.structured_content["state"] == {"message": "Hello"}
        assert "MCP Apps host" in result.content[0].text
        invalid = await client.call_tool(
            tool.name, {"version": "latest"}, raise_on_error=False
        )
        assert invalid.is_error


@pytest.mark.asyncio
async def test_callback_is_app_visible_but_direct_call_still_checks_authorization(
    resources,
):
    database, state, registry, _, server, identity = resources
    source = FastMCP("math")
    calls = []

    @source.tool(meta={"custom": {"origin": "math"}})
    def increment(value: int) -> dict:
        calls.append(value)
        return {"value": value + 1}

    ConfigService.add_server_to_config(
        "math", {"type": "stdio", "command": "fixture-not-run"}
    )
    from types import SimpleNamespace

    manager = SimpleNamespace(server_info={"math": server_info(source)})
    resolver = AppToolResolver(database, manager, registry.authorize)
    wire = {
        "view": {
            "type": "Button",
            "label": "Increment",
            "onClick": {
                "action": "toolCall",
                "tool": "increment",
                "arguments": {"value": "{{ count }}"},
            },
        },
        "state": {"count": 7},
    }
    upload_app(
        database,
        slug="demo",
        display_name="Demo",
        version="1.1.0",
        content=json.dumps(wire).encode(),
        filename="app.json",
    )
    await replace_app_bindings(
        database,
        resolver,
        slug="demo",
        version="1.1.0",
        bindings=[
            AppToolBinding(alias="increment", server_name="math", tool_name="increment")
        ],
    )
    validate = await publication_validator(database, resolver, "demo", "1.1.0")
    publish_app_version(
        database,
        slug="demo",
        version="1.1.0",
        reviewed=True,
        validate_bindings=validate,
    )
    server.add_provider(AppToolsProvider(registry, resolver))
    name = app_callback_name(identity.app_id, "1.1.0", "increment")
    async with Client(server) as client:
        opened = await client.call_tool(
            app_open_name(replace(identity, version="1.1.0")), {}
        )
        assert (
            opened.structured_content["view"]["children"][0]["onClick"]["tool"] == name
        )
        tool = next(item for item in await client.list_tools() if item.name == name)
        assert tool.meta["ui"]["visibility"] == ["app"]
        assert tool.meta["custom"] == {"origin": "math"}
        result = await client.call_tool(name, {"value": 7})
        assert result.structured_content == {"value": 8}
        assert calls == [7]
        state["authorized"] = False
        denied = await client.call_tool(name, {"value": 9}, raise_on_error=False)
        assert denied.is_error
        assert calls == [7]
        state["authorized"] = True
        manager.server_info["math"]["status"] = "stopped"
        denied = await client.call_tool(name, {"value": 9}, raise_on_error=False)
        assert denied.is_error
        assert calls == [7]
        manager.server_info["math"]["status"] = "running"
        withdraw_app_version(database, slug="demo", version="1.1.0")
        denied = await client.call_tool(name, {"value": 9}, raise_on_error=False)
        assert denied.is_error
        assert calls == [7]


@pytest.mark.asyncio
async def test_retained_open_tool_does_not_extend_permission(resources):
    database, state, registry, _, _, identity = resources
    provider = AppToolsProvider(
        registry, AppToolResolver(database, object(), registry.authorize)
    )
    tool = await provider.get_tool(app_open_name(identity))
    assert tool is not None
    state["authorized"] = False
    assert await provider.list_tools() == []
    with pytest.raises(Exception, match="不可用"):
        await tool.run({})


def test_callback_names_are_version_and_app_scoped_and_bounded():
    assert app_callback_name(1, "1.0.0", "save") != app_callback_name(
        2, "1.0.0", "save"
    )
    assert app_callback_name(1, "1.0.0", "save") != app_callback_name(
        1, "1.1.0", "save"
    )
    assert len(app_callback_name(1, "1.0.0", "x" * 128)) < 128
