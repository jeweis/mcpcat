from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastmcp import FastMCP
from starlette.routing import Mount
from fastmcp.server.openapi import RouteMap, MCPType
import httpx
from typing import List,Dict, Callable
from contextlib import asynccontextmanager
import os
import json
from pathlib import Path
from app.core.config import settings


def load_config():
    """加载配置文件"""
    # 从config.py获取配置文件路径
    config_path = settings.mcpcat_config_path
    print(f"配置文件路径: {config_path}")
    
    # 如果是相对路径，则相对于项目根目录
    if not os.path.isabs(config_path):
        config_file = Path(__file__).parent / config_path
    else:
        config_file = Path(config_path)
    
    if config_file.exists():
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"读取配置文件失败: {e}")
            return {}
    else:
        # 如果配置文件不存在，创建空配置文件
        config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump({}, f, indent=2)
        print(f"已创建配置文件: {config_file}")
        return {}

## lifespan管理
# 任务存储 - 存储 asynccontextmanager 函数
lifespan_tasks: Dict[str, Callable] = {}
# 应用状态
app_started = False
# 存储运行中的任务上下文
running_contexts = {}

@asynccontextmanager
async def lifespan_manager(app: FastAPI):
    global app_started
    
    print("应用启动中...")
    app_started = True
    
    # 启动所有任务
    for task_name, task_lifespan in lifespan_tasks.items():
        ctx = task_lifespan(app)
        await ctx.__aenter__()
        running_contexts[task_name] = ctx
        print(f"✓ 任务 {task_name} 启动成功")
    
    yield
    
    print("应用关闭中...")
    app_started = False
    
    # 关闭所有任务 - 包括动态添加的
    for task_name, ctx in list(running_contexts.items()):
        try:
            await ctx.__aexit__(None, None, None)
            print(f"✓ 任务 {task_name} 关闭成功")
        except Exception as e:
            print(f"✗ 任务 {task_name} 关闭失败: {e}")
    
    running_contexts.clear()


# 从配置文件加载MCP服务器列表
print("Loading MCP server list...")
mcpServerList = load_config()
print("MCP server list loaded.")
print(mcpServerList)
app_mount_list=[]

def add_mcp_server(key, value):
    
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
    lifespan_tasks[key] = mcp_app.lifespan

## 遍历mcpServerList的字段
for key, value in mcpServerList.items():
    add_mcp_server(key, value)

app = FastAPI(
    title="MCPCat",
    description="MCP聚合平台 - 支持多种MCP协议的统一管理平台",
    version="0.1.0",
    lifespan=lifespan_manager
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
    uvicorn.run(app, host=settings.host, port=settings.port)