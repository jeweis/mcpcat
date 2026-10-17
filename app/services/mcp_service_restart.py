"""重启时先准备/验证候选，旧实例完成关闭后才切换明确代际。"""

import logging
from typing import Any

from app.services.config_service import ConfigService
from app.services.mcp_factory import MCPServerFactory
from app.services.proxy_subscriptions import attach_proxy_subscriptions
from app.services.proxy_task_attachment import attach_proxy_tasks

logger = logging.getLogger(__name__)


def _prepare(manager: Any, name: str, config: dict) -> dict | None:
    mcp = MCPServerFactory.create_server(name, config)
    if mcp is None:
        return None
    subscriptions = attach_proxy_subscriptions(manager, name, mcp, config)
    attach_proxy_tasks(mcp, config, subscriptions)
    runtime = getattr(manager, "skills_runtime", None)
    if runtime is not None:
        attachment = runtime.attach_service(name, mcp)
        if subscriptions is not None:
            subscriptions.attach_registry(attachment)
        else:
            attachment.install_subscriptions()
    http, sse = mcp.http_app(path="/"), mcp.http_app(path="/", transport="sse")
    return {
        "config": config,
        "mcp": mcp,
        "mcp_app": http,
        "sse_app": sse,
        "status": "loaded",
        "auth_status": "none",
    }


async def restart_source(manager: Any, name: str, new_config: dict | None) -> bool:
    """不为无效候选停掉可用旧服务；保存失败时恢复旧生命周期。"""
    previous = manager.server_info.get(name)
    if previous is None:
        return False
    if (
        new_config is not None
        and not ConfigService.validate_server_config(new_config)[0]
    ):
        return False
    config = new_config if new_config is not None else previous["config"]
    try:
        candidate = _prepare(manager, name, config)
        if candidate is None:
            return False
        if not await manager.stop_server(name):
            return False
        if new_config is not None and not ConfigService.update_server_config(
            name, config
        ):
            await manager.start_server(name)
            return False
        manager.server_info[name] = {**previous, **candidate}
        manager.lifespan_tasks[name] = candidate["mcp_app"].lifespan
        return await manager.start_server(name)
    except Exception as error:
        logger.error("MCP 重启失败：%s (%s)", name, type(error).__name__)
        manager._update_server_status(name, "failed", "MCP 重启失败")
        return False
