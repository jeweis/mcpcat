"""全局管理设置API"""

import logging
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)

router = APIRouter()

# 合法 origin：http(s)://host[:port]，无尾斜杠、无路径
_ORIGIN_PATTERN = re.compile(r"^https?://[a-zA-Z0-9.\-]+(:\d+)?$")


class BaseUrlResponse(BaseModel):
    """规范域名响应"""

    public_base_url: str = ""


class UpdateBaseUrlRequest(BaseModel):
    """更新规范域名请求"""

    public_base_url: str = ""


@router.get("/admin/settings/base-url", response_model=BaseUrlResponse)
async def get_base_url():
    """获取对外规范域名（复制 MCP 地址时拼接使用）"""
    app_config = ConfigService.get_setting_section("app", {}) or {}
    return BaseUrlResponse(public_base_url=app_config.get("public_base_url") or "")


@router.put("/admin/settings/base-url", response_model=BaseUrlResponse)
async def update_base_url(payload: UpdateBaseUrlRequest):
    """更新对外规范域名（需写权限），校验为合法 origin 后持久化"""
    value = (payload.public_base_url or "").strip()
    if value and not _ORIGIN_PATTERN.fullmatch(value):
        raise HTTPException(
            status_code=400,
            detail="域名格式无效：需为 http(s)://host[:port]，无尾斜杠",
        )

    app_config = ConfigService.get_setting_section("app", {}) or {}
    app_config["public_base_url"] = value or None
    if not ConfigService.update_setting_section("app", app_config):
        raise HTTPException(status_code=500, detail="设置保存失败")
    logger.info(f"规范域名已更新: {value or '(空，回退浏览器 origin)'}")
    return BaseUrlResponse(public_base_url=value)
