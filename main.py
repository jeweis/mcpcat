from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastmcp import FastMCP
from starlette.routing import Mount


# Create your FastMCP server as well as any tools, resources, etc.
mcp = FastMCP("McpcatServer")
# Create the ASGI app
mcp_app = mcp.http_app(path='/')

# For SSE transport (deprecated)
sse_app = mcp.http_app(path="/", transport="sse")


app = FastAPI(
    title="MCPCat",
    description="MCP聚合平台 - 支持多种MCP协议的统一管理平台",
    version="0.1.0",
    lifespan=mcp_app.lifespan
)
app.mount("/mcp", mcp_app)
app.mount("/sse", sse_app)
@app.get("/health")
async def health_check():
    """健康检查接口"""
    return {"message": "OK"}

@app.get("/")
async def root():
    """根路径"""
    return {"message": "Welcome to MCPCat - MCP聚合平台"}

@mcp.tool
def add(a: int, b: int) -> int:
    """Adds two integer numbers together."""
    return a + b

mcpConfig = {
    "mcpServers": {
        "fetch": {
      "command": "uvx",
      "args": [
        "mcp-server-fetch"
      ]
    },
        "sequential-thinking": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-sequential-thinking"
      ]
    }
    }
}
proxy = FastMCP.as_proxy(mcpConfig, name="Config-Based Proxy")
mcp.mount("app", proxy)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)