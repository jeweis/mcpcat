"""可信 STDIO 测试服务，不读写业务数据。"""

from fastmcp import FastMCP

server = FastMCP("STDIO fixture")


@server.tool()
def echo(value: str) -> str:
    """Return the input."""
    return value


if __name__ == "__main__":
    server.run(show_banner=False, log_level="ERROR")
