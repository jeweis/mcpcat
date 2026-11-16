"""管理员诊断查询；不读取任意文件或执行上游请求。"""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.services.config_service import ConfigService
from app.services.diagnostic_logs import DiagnosticLogs
from app.services.security_service import security_service

router = APIRouter()


def protect_config(store: DiagnosticLogs) -> None:
    """将当前有效凭证和上游配置加入脱敏集合，不输出配置内容。"""
    store.protect(ConfigService.load_raw_config())
    store.protect(
        {
            "api_keys": [
                {"key": item.key} for item in security_service.get_all_api_keys()
            ]
        }
    )


@router.get("/admin/logs")
async def get_diagnostic_logs(
    request: Request,
    response: Response,
    level: Literal["ALL", "INFO", "WARNING", "ERROR", "CRITICAL"] = "ALL",
    server: str = Query(default="", max_length=256),
    search: str = Query(default="", max_length=256),
    before: int | None = Query(default=None, ge=1),
    limit: int = Query(default=100, ge=1, le=200),
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict:
    """WRITE 权限由 /api/admin 中间件强制，错误链仅在此接口可见。"""
    since = (
        since.replace(tzinfo=timezone.utc) if since and since.tzinfo is None else since
    )
    until = (
        until.replace(tzinfo=timezone.utc) if until and until.tzinfo is None else until
    )
    if since and until and since > until:
        raise HTTPException(422, "开始时间不能晚于结束时间")
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    store = request.app.state.diagnostic_logs
    protect_config(store)
    return store.query(
        level=level,
        server=server,
        search=search,
        before=before,
        limit=limit,
        since=since,
        until=until,
    )
