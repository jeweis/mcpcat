"""mcpcat 进程入口。"""

import logging

import uvicorn

from app.application import create_app
from app.core.config import settings

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

app = create_app()
server_manager = app.state.server_manager


def load_config():
    """保留旧入口的 MCP 配置读取函数。"""

    from app.services.config_service import ConfigService

    return ConfigService.load_mcp_servers_config()


def add_mcp_server(key, value):
    """保留旧入口的 MCP 服务添加函数。"""

    return server_manager.add_mcp_server(key, value)


def main() -> None:
    """启动单 Worker Uvicorn 服务。"""

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        timeout_graceful_shutdown=10,
        timeout_keep_alive=5,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
