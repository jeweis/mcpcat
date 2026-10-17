"""FastMCP 公开运行配置的旧客户端兼容默认值。"""

import fastmcp

from app.core.config import settings


def configure_mcp_runtime() -> None:
    """旧 HTTP 会话默认不因新 SDK 空闲期限失效，可配置有限秒数。"""
    timeout = settings.mcpcat_mcp_http_session_idle_timeout
    fastmcp.settings.http_session_idle_timeout = timeout if timeout > 0 else None
