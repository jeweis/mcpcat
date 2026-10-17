"""可信能力测试来源：只报告协议状态，不读取用户数据。"""

import os

from fastmcp import Context, FastMCP

server = FastMCP("stdio capability fixture")


@server.tool()
async def inspect_capabilities(ctx: Context) -> dict:
    """返回当前请求声明和进程标识，供隔离与释放检查。"""
    capability = ctx.session.client_capabilities.elicitation
    return {
        "elicitation": (
            capability.model_dump(by_alias=True, exclude_none=True)
            if capability is not None
            else None
        ),
        "protocol": ctx.request_context.protocol_version,
        "pid": os.getpid(),
    }


if __name__ == "__main__":
    server.run(show_banner=False, log_level="ERROR")
