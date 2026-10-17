"""机器授权通过真实 MCP 连接检测，不进入浏览器授权或旧 token 刷新。"""

from copy import deepcopy
from typing import Any

from app.services.proxy_clients import make_proxy_client_factory


async def detect_machine_auth(
    servers: dict[str, Any], name: str, config: dict[str, Any]
) -> None:
    """检测结果只更新未变更的原实例；不保存令牌或记录秘密。"""
    original = servers.get(name)
    if original is None or original.get("config") is not config:
        return
    snapshot = deepcopy(config)
    status = original.get("status")

    def is_current() -> bool:
        return (
            servers.get(name) is original
            and original.get("config") is config
            and config == snapshot
            and original.get("status") == status
        )

    try:
        async with make_proxy_client_factory(snapshot)():
            pass  # SDK 进入连接时完成现代发现或旧握手，不执行业务工具。
    except Exception:
        if is_current():
            original.update(
                auth_status="auth_required",
                status="failed",
                error="机器认证连接失败，请检查来源地址、授权配置和网络",
            )
        return
    if is_current():
        original["auth_status"] = "authorized"
