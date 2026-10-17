"""可信演示使用公开 MCP 接口，工具/提示/资源和 App 保持同连接。"""

import importlib.util
import asyncio
import os
import sys
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP


def load_demo():
    """加载仓库示例，不安装或执行用户上传的内容。"""
    path = Path(__file__).parents[1] / "examples/time-skills/server.py"
    spec = importlib.util.spec_from_file_location("time_demo", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_time_demo_composition() -> None:
    upstream = FastMCP("isolated time")

    @upstream.tool()
    def get_current_time(timezone: str) -> dict:
        return {"timezone": timezone, "fixture": True}

    @upstream.tool()
    def convert_time(source_timezone: str, time: str, target_timezone: str) -> dict:
        return {
            "source_timezone": source_timezone,
            "time": time,
            "target_timezone": target_timezone,
        }

    demo = load_demo()
    async with Client(demo.create_time_demo(upstream)) as client:
        tools = await client.list_tools()
        assert {t.name for t in tools} == {
            "get_current_time",
            "convert_time",
            "open_world_clock",
        }
        app = next(t for t in tools if t.name == "open_world_clock")
        assert app.meta["ui"]["resourceUri"] == demo.APP_URI
        prompts = await client.list_prompts()
        assert {p.name for p in prompts} == {"meeting_planner", "deadline_localizer"}
        for prompt in prompts:
            arguments = {
                "source_timezone": "Asia/Shanghai",
                "target_timezone": "Europe/London",
            }
            if prompt.name == "deadline_localizer":
                arguments["deadline"] = "23:00"
            response = await client.get_prompt(prompt.name, arguments)
            assert response.messages[0].role == "user"
            assert "convert_time" in response.messages[0].content.text
        resources = await client.list_resources()
        assert demo.APP_URI in {str(r.uri) for r in resources}
        html = await client.read_resource(demo.APP_URI)
        assert "get_current_time" in html[0].text
        assert html[0].mime_type == "text/html;profile=mcp-app"
        result = await client.call_tool("get_current_time", {"timezone": "UTC"})
        assert result.structured_content == {"timezone": "UTC", "fixture": True}


@pytest.mark.asyncio
async def test_demo_proxy_releases_each_stdio_process(monkeypatch) -> None:
    """演示生命周期显式持有一个进程，重复使用且退出后真正释放。"""
    demo = load_demo()
    config = demo.TIME_CONFIG["mcpServers"]["time"]
    assert config["keep_alive"] is False
    monkeypatch.setitem(config, "command", sys.executable)
    monkeypatch.setitem(
        config,
        "args",
        [
            str(Path(__file__).parent / "fixtures/mcp_stdio_logging.py"),
            "legacy",
        ],
    )
    pids = []
    async with asyncio.timeout(30), Client(demo.create_time_demo()) as client:
        for _ in range(2):
            result = await client.call_tool("emit", {"owner": "demo-fixture"})
            assert result.data["protocol"] == "2025-11-25"
            pids.append(result.data["pid"])
            os.kill(pids[-1], 0)
        assert pids[0] == pids[1]
    with pytest.raises(ProcessLookupError):
        os.kill(pids[-1], 0)
