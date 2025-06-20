"""健康检查和基础监控API"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health_check():
    """健康检查接口 - 与原有逻辑完全一致"""
    return {"message": "OK"}


@router.get("/status")
async def get_basic_status():
    """获取基础系统状态"""
    return {
        "app_name": "MCPCat",
        "version": "0.1.0",
        "description": "MCP聚合平台 - 支持多种MCP协议的统一管理平台",
        "status": "running"
    } 