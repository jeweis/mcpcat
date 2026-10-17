"""原始框架 hash 只在来源内解别名，不能泄漏到 Catalog 全局 hash 路由。"""

from uuid import uuid4

from fastmcp.server.providers.addressing import hash_tool, hashed_backend_name
from fastmcp.tools import Tool

from app.services.catalog_app_identity import CatalogAppIdentity
from app.services.catalog_app_sources import CatalogAppSource
from app.services.catalog_app_tools import callback_bindings, scoped_metadata


def test_namespaced_framework_hash_is_verified_instead_of_guessing_separator():
    source = CatalogAppSource(
        "left", object(), CatalogAppIdentity(str(uuid4()), str(uuid4()))
    )
    tool = Tool(
        name="nested_prefix_save",
        parameters={"type": "object"},
        meta={
            "ui": {"visibility": ["app"]},
            "fastmcp": {
                "app": "Shared",
                "tool_hash": hash_tool("Shared", "save"),
                "unknown": True,
            },
        },
    )
    bindings = callback_bindings(source, [tool])
    assert bindings[hashed_backend_name("Shared", "save")] == source.identity.callback(
        tool.name
    )
    assert bindings[tool.name] == source.identity.callback(tool.name)
    metadata = scoped_metadata(source, tool, callback=True)
    expected = dict(tool.get_meta()["fastmcp"])
    expected.pop("tool_hash")
    assert metadata["fastmcp"] == expected
    assert tool.meta["fastmcp"]["tool_hash"] == hash_tool("Shared", "save")


def test_ambiguous_same_source_hash_alias_is_not_mapped():
    source = CatalogAppSource(
        "left", object(), CatalogAppIdentity(str(uuid4()), str(uuid4()))
    )
    tools = [
        Tool(
            name=name,
            parameters={},
            meta={
                "ui": {"visibility": ["app"]},
                "fastmcp": {"app": "Shared", "tool_hash": hash_tool("Shared", "save")},
            },
        )
        for name in ("a_save", "b_save")
    ]
    bindings = callback_bindings(source, tools)
    assert hashed_backend_name("Shared", "save") not in bindings
    assert set(bindings) == {"a_save", "b_save"}
