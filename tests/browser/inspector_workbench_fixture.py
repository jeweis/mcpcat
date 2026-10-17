"""为统一测试台提供独立的真实 MCP 端点，不启动用户服务。"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastmcp import Context, FastMCP
from fastmcp_tasks import TasksExtension
from mcp import types
from mcp_types import (
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitRequestURLParams,
    InputRequiredResult,
)


def attach_workbench(application):
    """注册含资源、提示、普通交互和 Tasks 的测试专用端点。"""
    from app.services.config_service import ConfigService
    from app.services.registry_subscriptions import (
        RegistrySubscriptions,
        VisibleSnapshot,
    )

    server = FastMCP("workbench")
    server.add_extension(TasksExtension(url="memory://", name="browser-workbench"))

    def revision() -> int:
        return ConfigService.get_setting_section("browser_workbench", {}).get(
            "revision", 0
        )

    async def snapshot() -> VisibleSnapshot:
        return VisibleSnapshot(resources={"guide://intro": str(revision())})

    server.add_middleware(
        RegistrySubscriptions(
            application.state.database,
            snapshot,
            lambda: True,
        )
    )

    @server.tool()
    def notify_demo() -> dict:
        """只更新隔离夹具资料，提交后通过真实 MCP 订阅发送资源变化。"""
        value = revision() + 1
        ConfigService.update_setting_section("browser_workbench", {"revision": value})
        return {"revision": value}

    @server.resource("ui://workbench/demo", mime_type="text/html;profile=mcp-app")
    def app_ui() -> str:
        return Path(__file__).with_name("workbench_app.html").read_text()

    @server.tool(meta={"ui": {"resourceUri": "ui://workbench/demo"}})
    def open_demo(message: str = "动态入口已执行") -> dict:
        return {"message": message}

    @server.resource("guide://intro", mime_type="text/markdown")
    def intro() -> str:
        return f"# 测试台说明\n\n这是一份隔离测试资料。版本 {revision()}。"

    @server.resource("greeting://{name}")
    def greeting(name: str) -> str:
        return f"你好，{name}"

    @server.prompt()
    def welcome(name: str) -> str:
        return f"请向 {name} 介绍 MCP 测试台。"

    @server.completion
    def suggest(ref, argument, context):
        return [
            name
            for name in ("Alice", "Alex", "Bob")
            if name.lower().startswith(argument.value.lower())
        ]

    @server.tool()
    async def confirm_url(ctx: Context):
        if ctx.input_responses is None:
            return InputRequiredResult(
                input_requests={
                    "approval": ElicitRequest(
                        params=ElicitRequestURLParams(
                            message="浏览器测试：请明确确认、拒绝或取消；无需打开测试地址。",
                            url="https://example.invalid/mcpcat-confirm",
                        )
                    ),
                }
            )
        return {"action": ctx.input_responses["approval"].action}

    @server.tool()
    async def progress_demo(ctx: Context) -> str:
        for index in range(3):
            await ctx.report_progress(index, 2, "fixture progress")
            await asyncio.sleep(0.2)
        return "进度测试完成"

    @server.tool()
    def echo(message: str) -> str:
        return message

    @server.tool()
    async def sample_client(ctx: Context):
        """仅验收用户手工响应，不连接模型。"""
        if ctx.input_responses is None:
            return types.InputRequiredResult(input_requests={
                "sample": types.CreateMessageRequest(method="sampling/createMessage",
                    params=types.CreateMessageRequestParams(max_tokens=10, messages=[
                        types.SamplingMessage(role="user", content=types.TextContent(
                            type="text", text="请手工填写 fixture 响应")),
                    ])),
            })
        return ctx.input_responses["sample"].model_dump(mode="json", by_alias=True)

    @server.tool()
    async def client_roots(ctx: Context):
        """读取客户端明确声明的目录，不访问服务器文件。"""
        if ctx.input_responses is None:
            return types.InputRequiredResult(input_requests={
                "roots": types.ListRootsRequest(method="roots/list"),
            })
        return ctx.input_responses["roots"].model_dump(mode="json", by_alias=True)

    @server.tool(task=True)
    async def delayed(seconds: int = 2) -> dict:
        await asyncio.sleep(min(max(seconds, 0), 30))
        return {"total": 42}

    @server.tool(task=True)
    async def ask_city(ctx: Context):
        if ctx.input_responses is None:
            return InputRequiredResult(
                input_requests={
                    "city": ElicitRequest(
                        method="elicitation/create",
                        params=ElicitRequestFormParams(
                            message="请输入要查询的城市（测试数据，不访问外部服务）",
                            requested_schema={
                                "type": "object",
                                "properties": {"city": {"type": "string"}},
                                "required": ["city"],
                            },
                        ),
                    )
                }
            )
        answer = ctx.input_responses["city"]
        return answer.content if answer.action == "accept" else {"declined": True}

    ConfigService.add_server_to_config(
        "workbench",
        {
            "type": "stdio",
            "command": "fixture-never-executed",
            "require_auth": True,
        },
    )
    http_app = server.http_app(path="/")
    original_lifespan = application.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with original_lifespan(app), http_app.router.lifespan_context(http_app):
            yield

    application.router.lifespan_context = lifespan
    application.mount("/mcp/workbench", http_app)
