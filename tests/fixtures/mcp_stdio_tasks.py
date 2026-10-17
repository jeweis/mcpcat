"""可信 Tasks STDIO 夹具：仅返回本进程身份，不访问业务数据。"""

import asyncio
import os

from fastmcp import Context, FastMCP
from fastmcp_tasks import TasksExtension
from mcp_types import ElicitRequest, ElicitRequestFormParams, InputRequiredResult

server = FastMCP("STDIO Tasks fixture")
server.add_extension(TasksExtension(url="memory://", name="stdio-tasks-fixture"))


@server.tool(task=True)
async def identify(delay: float = 0) -> dict:
    """Return the worker process identity after a bounded fixture delay."""
    await asyncio.sleep(min(max(delay, 0), 10))
    return {"pid": os.getpid()}


@server.tool(task=True)
async def confirm_identity(ctx: Context):
    """Request input in the original worker; report its identity on resume."""
    if ctx.input_responses is None:
        return InputRequiredResult(
            input_requests={
                "confirm": ElicitRequest(
                    method="elicitation/create",
                    params=ElicitRequestFormParams(
                        message=f"Confirm worker {os.getpid()}",
                        requested_schema={
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                        },
                    ),
                )
            }
        )
    answer = ctx.input_responses["confirm"]
    return {"pid": os.getpid(), "action": answer.action, "content": answer.content}


if __name__ == "__main__":
    server.run(show_banner=False, log_level="ERROR")
