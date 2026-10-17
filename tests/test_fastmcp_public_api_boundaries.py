"""固定 4.1 公开 API 的组合边界，避免误用 Namespace 或运行后注册扩展。"""

from contextlib import asynccontextmanager

import pytest
from fastmcp import FastMCP
from fastmcp.resources import Resource
from fastmcp.server.extensions import ServerExtension
from fastmcp.server.providers import FastMCPProvider, Provider
from fastmcp.server.transforms.namespace import Namespace
from fastmcp.tools import Tool


@pytest.mark.asyncio
async def test_namespace_does_not_rewrite_arbitrary_app_metadata():
    source = FastMCP("source")
    source.add_tool(
        Tool.from_function(
            lambda: "open",
            name="open",
            meta={"ui": {"resourceUri": "ui://source/view.html"}},
        )
    )
    source.add_resource(
        Resource.from_function(
            lambda: "<h1>Original</h1>", "ui://source/view.html", name="view"
        )
    )
    raw = FastMCPProvider(source)
    wrapped = raw.wrap_transform(Namespace("tenant"))
    tool = await wrapped.get_tool("tenant_open")
    resources = await wrapped.list_resources()
    assert tool.meta["ui"]["resourceUri"] == "ui://source/view.html"
    assert str(resources[0].uri) == "ui://tenant/source/view.html"
    assert await wrapped.get_resource(tool.meta["ui"]["resourceUri"]) is None
    assert (await raw.get_tool("open")).name == "open"


@pytest.mark.asyncio
async def test_extension_clone_lifespan_and_registration_boundary():
    events = []

    class Extension(ServerExtension):
        identifier = "io.mcpcat/api-probe"

        @asynccontextmanager
        async def lifespan(self):
            events.append((self.server.name, "enter"))
            try:
                yield
            finally:
                events.append((self.server.name, "exit"))

    class OtherExtension(ServerExtension):
        identifier = "io.mcpcat/api-other"

    first, second = FastMCP("first"), FastMCP("second")
    extension = Extension()
    first.add_extension(extension)
    with pytest.raises(ValueError, match="already bound"):
        second.add_extension(extension)
    second.add_extension(extension.clone())
    app = first.http_app(path="/")
    async with app.lifespan(app):
        with pytest.raises(RuntimeError, match="already started"):
            first.add_extension(OtherExtension())
    other = second.http_app(path="/")
    async with other.lifespan(other):
        pass
    assert events == [
        ("first", "enter"),
        ("first", "exit"),
        ("second", "enter"),
        ("second", "exit"),
    ]


@pytest.mark.asyncio
async def test_wrapped_provider_lifespan_is_owned_by_public_server_context():
    events = []

    class Source(Provider):
        @asynccontextmanager
        async def lifespan(self):
            events.append("enter")
            try:
                yield
            finally:
                events.append("exit")

    server = FastMCP("owner", providers=[Source().wrap_transform(Namespace("scope"))])
    app = server.http_app(path="/")
    async with app.lifespan(app):
        assert events == ["enter"]
    assert events == ["enter", "exit"]
