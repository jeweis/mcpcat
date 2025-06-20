"""
MCPCat主应用 - 重构版本
保持与原有逻辑完全一致，但使用模块化的服务类
"""

import logging
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from typing import Dict, Callable

# 导入新的服务类
from app.core.config import settings
from app.services.server_manager import MCPServerManager
from app.api import health, servers

# 保持与原代码完全一致的全局变量
lifespan_tasks: Dict[str, Callable] = {}
app_started = False
running_contexts = {}  # 保持兼容性，虽然不再使用
app_mount_list = []    # 保持兼容性，虽然不再使用

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("MCPCat")

# 创建全局服务器管理器
server_manager = MCPServerManager()


# 保持向后兼容的函数
def load_config():
    """保持向后兼容的配置加载函数"""
    from app.services.config_service import ConfigService
    return ConfigService.load_config()


def add_mcp_server(key, value):
    """保持向后兼容的服务器添加函数"""
    return server_manager.add_mcp_server(key, value)


@asynccontextmanager
async def lifespan_manager(app: FastAPI):
    """
    应用生命周期管理器 - 与原有逻辑完全一致
    现在使用服务器管理器，但保持相同的行为
    """
    global app_started
    
    app_started = True
    
    # 使用服务器管理器的统一生命周期管理
    # 这会产生与原有 AsyncExitStack 逻辑完全相同的行为
    async with server_manager.create_unified_lifespan(app):
        yield
    
    app_started = False


# 加载配置和创建服务器 - 保持与原逻辑完全一致
print("Loading MCP server list...")
mcpServerList = load_config()
print("MCP server list loaded.")
print(mcpServerList)

# 创建服务器管理器并加载服务器
server_manager.load_servers_from_config()

# 为了保持兼容性，同步全局变量
lifespan_tasks = server_manager.get_lifespan_tasks()
app_mount_list = server_manager.get_mount_list()

# 创建 FastAPI 应用 - 与原逻辑完全一致
app = FastAPI(
    title="MCPCat",
    description="MCP聚合平台 - 支持多种MCP协议的统一管理平台",
    version="0.1.0",
    lifespan=lifespan_manager
)

# 存储服务器管理器到应用状态，供API使用
app.state.server_manager = server_manager

# 挂载所有服务器 - 与原逻辑完全一致
server_manager.mount_all_servers(app)

# 注册API路由 - 新增的功能，不影响原有行为
app.include_router(health.router, tags=["健康检查"])
app.include_router(servers.router, prefix="/api", tags=["服务器管理"])


# 保持原有的端点 - 与原逻辑完全一致
@app.get("/health")
async def health_check():
    """健康检查接口 - 保持与原有逻辑完全一致"""
    return {"message": "OK"}


@app.get("/")
async def root():
    """根路径 - 保持与原有逻辑完全一致"""
    return {"message": "Welcome to MCPCat - MCP聚合平台"}


# 新增的兼容性端点，提供与之前相同的信息
@app.get("/legacy/servers")
async def legacy_list_servers():
    """向后兼容的服务器列表接口"""
    servers_list = []
    for name in lifespan_tasks.keys():
        servers_list.append({
            "name": name,
            "mcp_endpoint": f"/mcp/{name}",
            "sse_endpoint": f"/sse/{name}",
            "status": "running" if app_started else "stopped"
        })
    return {"servers": servers_list, "total": len(servers_list)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)