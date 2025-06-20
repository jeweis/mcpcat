"""服务器监控和管理API"""

from fastapi import APIRouter, Request, HTTPException
from typing import Dict, Any

router = APIRouter()


@router.get("/servers")
async def list_servers(request: Request):
    """列出所有已配置的MCP服务器"""
    # 从应用状态中获取服务器管理器
    if hasattr(request.app.state, 'server_manager'):
        manager = request.app.state.server_manager
        server_status = manager.get_server_status()
        
        return {
            "servers": server_status,
            "total": len(server_status)
        }
    else:
        # 向后兼容：如果没有管理器，返回基础信息
        return {
            "servers": {},
            "total": 0,
            "message": "服务器管理器未初始化"
        }


@router.get("/servers/{server_name}")
async def get_server_detail(server_name: str, request: Request):
    """获取特定服务器的详细信息"""
    if hasattr(request.app.state, 'server_manager'):
        manager = request.app.state.server_manager
        server_status = manager.get_server_status()
        
        if server_name not in server_status:
            raise HTTPException(status_code=404, detail=f"服务器 '{server_name}' 不存在")
        
        return server_status[server_name]
    else:
        raise HTTPException(status_code=503, detail="服务器管理器未初始化")


@router.get("/servers/{server_name}/health")
async def check_server_health(server_name: str, request: Request):
    """检查特定服务器的健康状态"""
    if hasattr(request.app.state, 'server_manager'):
        manager = request.app.state.server_manager
        server_status = manager.get_server_status()
        
        if server_name not in server_status:
            raise HTTPException(status_code=404, detail=f"服务器 '{server_name}' 不存在")
        
        server_info = server_status[server_name]
        is_healthy = server_info['status'] == 'running'
        
        return {
            "server_name": server_name,
            "healthy": is_healthy,
            "status": server_info['status'],
            "error": server_info.get('error'),
            "endpoints": {
                "mcp": server_info['mcp_endpoint'],
                "sse": server_info['sse_endpoint']
            }
        }
    else:
        raise HTTPException(status_code=503, detail="服务器管理器未初始化") 