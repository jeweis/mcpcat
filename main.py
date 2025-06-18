from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastmcp import FastMCP
from starlette.routing import Mount
from fastmcp.server.openapi import RouteMap, MCPType
import httpx
from typing import List, Callable
from contextlib import asynccontextmanager


def merge_lifespans(lifespans: List[Callable[[FastAPI], object]]):
    @asynccontextmanager
    async def merged(app: FastAPI):
        stack = []
        try:
            for lifespan in lifespans:
                ctx = lifespan(app)
                await ctx.__aenter__()
                stack.append(ctx)
            yield
        finally:
            while stack:
                ctx = stack.pop()
                await ctx.__aexit__(None, None, None)
    return merged


mcpServerList={
    "fetch": {
      "type":"stdio",
      "command": "uvx",
      "args": [
        "mcp-server-fetch"
      ]
    },
      "mayna-openapi": {
      "type":"openapi",
      "spec_url": "https://api.jeweis.com/api/v3/api-docs/default",
      "api_base_url":"https://api.jeweis.com/api",
      "route_configs" : [{"methods":["GET"],"pattern":"^/user/userManage/.*"},{"methods":["GET"],"pattern":"^/goods/order/.*"}]
    },
    "sequential-thinking": {
      "type":"stdio",
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-sequential-thinking"
      ]
    }
    }
app_mount_list=[]
lifespan_list=[]
## 遍历mcpServerList的字段
for key, value in mcpServerList.items():
    mcp=None
    if value['type'] == 'stdio':
        ## 取value的env值，没有值时为空
        env = value.get('env', {})
        mcpConfig = {
            "mcpServers": {
                "default": {
            "command": value['command'],
            "args": value['args'],
            "env": env
            }
            }
        }
        mcp = FastMCP.as_proxy(mcpConfig, name="Config-Based Proxy")
    elif value['type'] == 'sse':
        headers = value.get('headers', {})
        url=value.get('url',"")
        mcpConfig = {
            "mcpServers": {
                "default": {
            "url": url,
            "transport":"sse",
            "headers": headers
            }
            }
        }
        mcp = FastMCP.as_proxy(mcpConfig, name="Config-Based Proxy")
    elif value['type'] == 'streamable-http':
        headers = value.get('headers', {})
        url=value.get('url',"")
        mcpConfig = {
            "mcpServers": {
                "default": {
            "url": url,
            "transport":"streamable-http",
            "headers": headers
            }
            }
        }
        mcp = FastMCP.as_proxy(mcpConfig, name="Config-Based Proxy")
    elif value['type'] == 'openapi':
        client = httpx.AsyncClient(base_url=value['api_base_url'])
        openapi_spec = httpx.get(value["spec_url"]).json()
        route_map_list=[]
        route_configs = value["route_configs"]
        for route_config in route_configs:
            route_map_list.append(RouteMap(
                methods=route_config['methods'],
                pattern=route_config['pattern'],
                mcp_type=MCPType.TOOL,
            ))
        # 添加默认的排除规则
        route_map_list.append(RouteMap(mcp_type=MCPType.EXCLUDE))
        mcp = FastMCP.from_openapi(
            openapi_spec=openapi_spec,
            client=client,
            name="openapi2mcpserver server",
            route_maps=route_map_list
        )
    
    mcp_app = mcp.http_app(path='/')
    sse_app = mcp.http_app(path="/", transport="sse") 
    app_mount_list.append({"path": f'''/mcp/{key}''', "app": mcp_app})
    app_mount_list.append({"path": f'''/sse/{key}''', "app": sse_app})
    lifespan_list.append(mcp_app.lifespan)
    
app = FastAPI(
    title="MCPCat",
    description="MCP聚合平台 - 支持多种MCP协议的统一管理平台",
    version="0.1.0",
    lifespan=merge_lifespans(lifespan_list)
)

## 遍历app_mount_list
for app_mount in app_mount_list:
    app.mount(app_mount['path'], app_mount['app'])

@app.get("/health")
async def health_check():
    """健康检查接口"""
    return {"message": "OK"}

@app.get("/")
async def root():
    """根路径"""
    return {"message": "Welcome to MCPCat - MCP聚合平台"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)