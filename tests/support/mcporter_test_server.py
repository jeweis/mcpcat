"""Gate B mcporter 真实端到端测试使用的本地 Streamable HTTP MCP。"""

from fastmcp import FastMCP

mcp = FastMCP("mcpcat-mcporter-e2e", instructions="Return deterministic echoes.")


@mcp.tool
def echo(message: str) -> str:
    """Return the supplied message."""

    return f"mcpcat:{message}"


if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=18765)
