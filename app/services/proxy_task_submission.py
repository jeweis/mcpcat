"""Tasks 上游单次提交与进度转发，不自动轮询或重放。"""

from typing import Any, Callable

from fastmcp_tasks.client import TasksClientExtension
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_REQUEST


async def submit_task_call(
    factory: Callable, identity: Callable, owner: str, params: Any, context: Any
) -> Any:
    """SDK 保留普通/任务结果；发送和转发进度前重新检查身份。"""

    async def progress(
        current: float, total: float | None, message: str | None
    ) -> None:
        if identity() != owner:
            raise MCPError(INVALID_REQUEST, "Task progress access changed")
        await context.report_progress(current, total, message)

    async with factory(extensions=[TasksClientExtension()]) as client:
        await client.list_tools()
        if identity() != owner:
            raise MCPError(INVALID_REQUEST, "Task access changed before dispatch")
        return await client.session.call_tool(
            name=params.name,
            arguments=params.arguments or {},
            meta=params.meta,
            allow_claimed=True,
            allow_input_required=True,
            input_responses=params.input_responses,
            request_state=params.request_state,
            progress_callback=progress,
        )
