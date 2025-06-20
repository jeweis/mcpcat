"""健康检查和基础监控API"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings

router = APIRouter()


@router.get("/health")
async def health_check():
    """健康检查接口 - 与原有逻辑完全一致"""
    return {"message": "OK"}


@router.get("/status")
async def get_basic_status():
    """获取基础系统状态"""
    return {
        "app_name": settings.app_name,
        "version": settings.app_version,
        "description": settings.description,
        "status": "running"
    } 