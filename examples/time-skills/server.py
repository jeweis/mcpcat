"""可信 Time 演示：保留官方时间工具，附加 Prompt、Resource 和 App。"""

from pathlib import Path
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastmcp import Client, FastMCP
from fastmcp.apps import AppConfig, UI_MIME_TYPE
from fastmcp.server.providers.proxy import FastMCPProxy

TIME_CONFIG = {
    "mcpServers": {
        "time": {
            "command": "uvx",
            "args": ["mcp-server-time==2026.10.10", "--local-timezone=Asia/Shanghai"],
            "keep_alive": False,
        }
    }
}
APP_URI = "ui://time-demo/world-clock.html"


def create_time_demo(upstream: FastMCP | None = None) -> FastMCP:
    """组合可信演示内容；不扫描或执行用户上传的文件。"""
    client = (
        Client(TIME_CONFIG, mode="legacy", timeout=60) if upstream is None else None
    )

    @asynccontextmanager
    async def lifecycle(server: FastMCP) -> AsyncIterator[dict]:
        if client is None:
            yield {}
            return
        try:
            async with client:
                yield {}
        finally:
            await client.close()

    server = FastMCP("time-demo", lifespan=lifecycle)
    server.mount(
        upstream
        if upstream is not None
        else FastMCPProxy(
            client_factory=lambda: client,
            name="official-time",
        )
    )

    @server.prompt()
    def meeting_planner(
        source_timezone: str,
        target_timezone: str,
        duration_minutes: str = "30",
    ) -> str:
        """比较两个时区今天的工作时间，寻找共同会议窗口。"""
        return (
            f"请安排 {source_timezone} 与 {target_timezone} 的 "
            f"{duration_minutes} 分钟会议。先用 get_current_time 获取两地当前日期，"
            "再用 convert_time 比较今天 09:00–18:00 的共同窗口，标注已过去的时段。"
            "列出两地日期、时间和 UTC 偏移，不自动创建会议。"
            "convert_time 只支持按来源时区今天转换 HH:MM；其他日期请明确说明限制。"
        )

    @server.prompt()
    def deadline_localizer(
        source_timezone: str,
        target_timezone: str,
        deadline: str,
    ) -> str:
        """将来源时区今天的截止时间换算为目标时区，并提示跨日。"""
        return (
            f"请将 {source_timezone} 今天 {deadline} 的截止时间换算到 "
            f"{target_timezone}。先获取来源时区今天日期，再调用 convert_time；"
            "输出双方完整日期、时间和 UTC 偏移，明确是否跨日。"
            "若用户其实需要其他日期，说明当前工具限制，不把今天的结果冒充未来日期。"
        )

    @server.resource("guide://time-demo/usage", mime_type="text/markdown")
    def usage() -> str:
        """提供同连接工具、Skills、提示和 App 的使用说明。"""
        return (
            "# 时间工作台\n\n工具：get_current_time、convert_time。\n\n"
            "提示模板：meeting_planner、deadline_localizer。\n\n"
            "应用：open_world_clock。应用通过当前 MCP 连接查询时间，不携带凭证。\n\n"
            "三个配套 Skills 由 mcpcat 管理并绑定本服务；服务器不执行 Skill 脚本。"
        )

    @server.tool(
        app=AppConfig(resourceUri=APP_URI),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    )
    def open_world_clock() -> dict:
        """打开世界时钟；在应用中选择时区，再明确查询当前时间。"""
        return {"message": "请选择时区并查询", "timezone": "Asia/Shanghai"}

    @server.resource(APP_URI, mime_type=UI_MIME_TYPE, meta={"ui": {"csp": {}}})
    def world_clock_ui() -> str:
        """返回固定 App 页面，无 CDN、外部网络或服务器脚本执行。"""
        return Path(__file__).with_name("world-clock.html").read_text(encoding="utf-8")

    return server


if __name__ == "__main__":
    create_time_demo().run(transport="stdio", show_banner=False)
