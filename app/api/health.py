"""健康检查和基础监控API"""

from fastapi import APIRouter, Request

from app.core.config import settings
from app.version import APP_VERSION

router = APIRouter()


@router.get("/health")
async def health_check(request: Request):
    """仅在存储和业务应用完整构建后报告 ready。"""

    ready = bool(getattr(request.app.state, "storage_ready", False))
    return {"message": "OK" if ready else "NOT_READY", "ready": ready}


@router.get("/status")
async def get_basic_status(request: Request):
    """获取基础系统状态"""
    return {
        "app_name": settings.app_name,
        "version": APP_VERSION,
        "description": settings.description,
        "status": (
            "running"
            if bool(getattr(request.app.state, "storage_ready", False))
            else "not_ready"
        ),
    }
