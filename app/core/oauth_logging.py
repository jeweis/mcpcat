"""Uvicorn OAuth 自动回调 access log 的查询信息脱敏。"""

import logging
from urllib.parse import unquote


class OAuthCallbackAccessFilter(logging.Filter):
    """保留回调路径/状态，避免标准 access log 输出授权 code/state。"""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            path, separator, _query = args[2].partition("?")
            if separator and "/api/oauth/callback/" in unquote(path):
                record.args = (*args[:2], f"{path}?[REDACTED]", *args[3:])
        return True


def install_oauth_access_filter() -> None:
    """应用工厂重复初始化时只安装一次，不改变其他路由的日志格式。"""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, OAuthCallbackAccessFilter) for item in logger.filters):
        logger.addFilter(OAuthCallbackAccessFilter())
