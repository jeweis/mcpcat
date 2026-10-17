"""统一测试台的真实 SDK 操作，不以管理索引替代协议。"""

import pytest
from types import SimpleNamespace
from fastmcp import Client, FastMCP

from app.services import inspector_skills as skills
from app.services import inspector_workbench as workbench


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_resources_templates_prompts_preserve_content(mode):
    server = FastMCP("workbench")

    @server.resource("data://guide", mime_type="text/markdown")
    def guide():
        return "# Guide"

    @server.resource("data://item/{name}")
    def item(name: str):
        return name

    @server.prompt
    def greet(name: str):
        return f"Hello {name}"

    async with Client(server, mode=mode) as client:
        resources = await workbench.listing(client, "resources")
        assert resources["supported"]
        assert resources["items"][0]["uri"] == "data://guide"
        assert (await workbench.listing(client, "templates"))["items"]
        read = await workbench.read_resource(client, "data://guide")
        assert read["contents"][0]["text"] == "# Guide"
        assert read["contents"][0]["mimeType"] == "text/markdown"
        template = await workbench.read_template(client, "data://item/{name}", {"name": "a b"})
        assert template["uri"] == "data://item/a%20b"
        prompts = await workbench.listing(client, "prompts")
        assert prompts["items"][0]["arguments"][0]["name"] == "name"
        prompt = await workbench.get_prompt(client, "greet", {"name": "reader"})
        assert prompt["messages"][0]["role"] == "user"
        assert prompt["messages"][0]["content"]["text"] == "Hello reader"


@pytest.mark.asyncio
async def test_unsupported_is_not_empty_error():
    # 框架空服务器仍声明核心能力，不能把空结果误当未支持。
    client = SimpleNamespace(server_capabilities=None)
    assert (await workbench.listing(client, "prompts"))["supported"] is False
    assert (await skills.list_skills(client, "auto", None))["supported"] is False
    async with Client(FastMCP("empty")) as connected:
        page = await workbench.listing(connected, "prompts")
        assert page["supported"] and page["items"] == []


@pytest.mark.asyncio
async def test_auto_provider_and_standard_error_never_falls_back(tmp_path, monkeypatch):
    from fastmcp.server.providers.skills import SkillProvider

    root = tmp_path / "demo"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: demo\ndescription: example\n---\nBody")
    server = FastMCP("compat")
    server.add_provider(SkillProvider(root))
    async with Client(server, mode="legacy") as client:
        page = await skills.list_skills(client, "auto", None)
        assert page["mode"] == "provider"
        assert len(page["skills"]) == 1
        monkeypatch.setattr(skills, "capabilities", lambda _: {
            "capabilities": {"resources": {}, "extensions": {skills.EXTENSION: {}}}
        })
        async def fail(*args):
            raise RuntimeError("standard failure")
        monkeypatch.setattr(skills, "request", fail)
        with pytest.raises(RuntimeError, match="standard failure"):
            await skills.list_skills(client, "auto", None)


@pytest.mark.asyncio
async def test_apps_discovery_and_preview_do_not_execute_tools():
    server = FastMCP("apps")
    calls = []

    @server.resource("ui://demo", mime_type="text/html;profile=mcp-app")
    def ui():
        return "<h1>Demo</h1>"

    @server.tool(meta={"ui": {"resourceUri": "ui://demo"}})
    def open_demo():
        calls.append(True)
        return {"value": 42}

    async with Client(server) as client:
        apps = await workbench.listing(client, "apps")
        assert apps["items"][0]["name"] == "open_demo"
        preview = await workbench.app_preview(client, "open_demo")
        assert preview["html"] == "<h1>Demo</h1>"
        assert preview["initial_result"] == {}
        assert not calls
        with pytest.raises(ValueError, match="可见范围"):
            await workbench.app_call(client, "open_demo", "hidden", {})
        result = await workbench.app_call(client, "open_demo", "open_demo", {})
        assert calls == [True]
        assert "structuredContent" in result
        opened = await workbench.app_open(client, "open_demo", {})
        assert opened["initial_result"]["structuredContent"] == {"value": 42}
        assert opened["initial_result_is_mcp"] is True
        assert opened["initial_arguments"] == {}
