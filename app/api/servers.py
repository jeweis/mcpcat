"""服务器监控和管理API"""

from fastapi import APIRouter, Request, HTTPException
from typing import Dict, Any
from pydantic import BaseModel

router = APIRouter()


class AddServerRequest(BaseModel):
    """添加服务器的请求模型"""
    name: str
    config: Dict[str, Any]
    
    class Config:
        schema_extra = {
            "example": {
                "name": "example-server",
                "config": {
                    "type": "stdio",
                    "command": "python",
                    "args": ["-m", "some_module"],
                    "enabled": True
                }
            }
        }


def _get_server_manager(request: Request):
    """获取服务器管理器，统一验证逻辑"""
    if not hasattr(request.app.state, 'server_manager'):
        raise HTTPException(status_code=503, detail="服务器管理器未初始化")
    return request.app.state.server_manager


def _validate_server_exists(manager, server_name: str):
    """验证服务器是否存在"""
    server_status = manager.get_server_status()
    if server_name not in server_status:
        raise HTTPException(status_code=404, detail=f"服务器 '{server_name}' 不存在")
    return server_status


@router.get("/servers")
async def list_servers(request: Request):
    """列出所有已配置的MCP服务器"""
    try:
        manager = _get_server_manager(request)
        server_status = manager.get_server_status()
        
        return {
            "servers": server_status,
            "total": len(server_status)
        }
    except HTTPException:
        # 如果是服务不可用，返回兼容性响应
        return {
            "servers": {},
            "total": 0,
            "message": "服务器管理器未初始化"
        }


@router.get("/servers/{server_name}")
async def get_server_detail(server_name: str, request: Request):
    """获取特定服务器的详细信息"""
    manager = _get_server_manager(request)
    server_status = _validate_server_exists(manager, server_name)
    
    return server_status[server_name]


@router.get("/servers/{server_name}/health")
async def check_server_health(server_name: str, request: Request):
    """检查特定服务器的健康状态"""
    manager = _get_server_manager(request)
    server_status = _validate_server_exists(manager, server_name)
    
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


@router.post("/servers")
async def add_server(server_request: AddServerRequest, request: Request):
    """动态添加新的MCP服务器并立即挂载"""
    manager = _get_server_manager(request)
    
    # 检查服务器名称是否已存在
    existing_servers = manager.get_server_status()
    if server_request.name in existing_servers:
        raise HTTPException(
            status_code=409, 
            detail=f"服务器 '{server_request.name}' 已存在"
        )
    
    # 验证配置格式
    required_fields = ['type']
    for field in required_fields:
        if field not in server_request.config:
            raise HTTPException(
                status_code=400,
                detail=f"配置缺少必需字段: {field}"
            )
    
    # 添加并挂载服务器
    try:
        success = await manager.add_and_mount_server(request.app, server_request.name, server_request.config)
        
        if success:
            # 获取更新后的服务器状态
            server_status = manager.get_server_status().get(server_request.name, {})
            
            return {
                "message": f"服务器 '{server_request.name}' 添加并挂载成功",
                "server_name": server_request.name,
                "status": server_status.get('status', 'mounted'),
                "type": server_request.config.get('type'),
                "command": server_request.config.get('command'),
                "args": server_request.config.get('args'),
                "url": server_request.config.get('url'),
                "enabled": server_request.config.get('enabled', True),
                "endpoints": {
                    "mcp": f"/mcp/{server_request.name}",
                    "sse": f"/sse/{server_request.name}"
                },
                "note": "服务器已成功添加、挂载并启动生命周期，可立即使用。"
            }
        else:
            # 获取错误信息
            server_info = manager.server_info.get(server_request.name, {})
            error_msg = server_info.get('error', '未知错误')
            
            raise HTTPException(
                status_code=500,
                detail=f"服务器 '{server_request.name}' 添加失败: {error_msg}"
            )
    except HTTPException:
        # 重新抛出 HTTPException
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"添加服务器时发生错误: {str(e)}"
        ) 