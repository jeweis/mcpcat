"""服务器管理器 - 封装MCP服务器管理逻辑"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, Callable, Dict, List, Optional, Set

from fastapi import FastAPI

from app.services.apps_mcp_settings import apps_path_reserved
from app.services.code_mode_settings import code_mode_path_reserved
from app.services.config_service import ConfigService
from app.services.mcp_factory import MCPServerFactory
from app.services.mcp_lifecycle import ServiceLifecycles, service_operation
from app.services.mcp_proxy_app import MCPProxyApp
from app.services.mcp_route_conflicts import mcp_route_occupied
from app.services.platform_endpoints import skills_path_reserved
from app.services.proxy_subscriptions import attach_proxy_subscriptions
from app.services.proxy_task_attachment import attach_proxy_tasks

logger = logging.getLogger(__name__)

RESERVED_NAMES = frozenset({"mcpcat"})


def is_reserved_name(name: str) -> bool:
    """
    检查名称是否为系统保留名。

    Args:
        name: 待检查的服务器名称

    Returns:
        bool: 是保留名或以下划线开头则为 True
    """
    return name in RESERVED_NAMES or name.startswith("_")


class MCPServerManager:
    """MCP服务器管理器 - 封装现有的服务器管理逻辑"""

    def __init__(self) -> None:
        # 与原有代码保持一致的数据结构
        self.lifespan_tasks: Dict[str, Callable] = {}  # FastMCP应用生命周期管理
        self.app_mount_list: List[Dict[str, Any]] = []  # 对应原有的 app_mount_list
        self.server_info: Dict[str, Dict[str, Any]] = {}  # 额外的服务器信息

        # 新增：用于管理动态添加的服务器生命周期
        self.app_started = False  # 应用是否已启动
        self.main_app: Optional[FastAPI] = None  # 主应用实例
        self.dynamic_tasks: Set[asyncio.Task] = set()  # 动态服务器任务集合
        self.catalog_service = None  # Catalog 服务实例（延迟初始化）
        self.catalog_refresh_task: Optional[asyncio.Task] = None
        self.lifecycle = ServiceLifecycles(self)
        from app.storage.change_notifications import ChangeNotifications

        self.runtime_changes = ChangeNotifications()

    def _update_server_status(
        self, server_name: str, status: str, error: Optional[str] = None
    ):
        """
        统一的服务器状态更新方法

        Args:
            server_name: 服务器名称
            status: 新状态
            error: 错误信息（可选）
        """
        if server_name in self.server_info:
            self.server_info[server_name]["status"] = status
            self.runtime_changes.publish({"mcp_runtime"})
            if error:
                self.server_info[server_name]["error"] = error
            elif "error" in self.server_info[server_name]:
                # 清除之前的错误信息
                del self.server_info[server_name]["error"]

    def load_servers_from_config(self) -> None:
        """
        从配置文件加载所有MCP服务器 - 保持与原有逻辑完全一致
        """
        # 加载MCP服务器配置
        mcp_server_list = ConfigService.load_mcp_servers_config()

        logger.info("正在加载 %d 个 MCP 服务", len(mcp_server_list))

        # 遍历配置并添加服务器 - 与原逻辑一致
        for key, value in mcp_server_list.items():
            if is_reserved_name(key):
                logger.warning(f"跳过保留名服务器 '{key}'，该名称被系统保留")
                continue
            self.add_mcp_server(key, value)

    def add_mcp_server(self, key: str, value: Dict[str, Any]) -> bool:
        """
        添加MCP服务器 - 正确的FastMCP生命周期管理

        Args:
            key: 服务器名称
            value: 服务器配置

        Returns:
            bool: 是否成功添加
        """
        try:
            # 使用工厂创建MCP服务器
            mcp = MCPServerFactory.create_server(key, value)

            if mcp is None:
                return False

            subscriptions = attach_proxy_subscriptions(self, key, mcp, value)
            attach_proxy_tasks(mcp, value, subscriptions)

            # 创建应用并正确获取生命周期
            runtime = getattr(self, "skills_runtime", None)
            if runtime is not None:
                attachment = runtime.attach_service(key, mcp)
                if subscriptions is not None:
                    subscriptions.attach_registry(attachment)
                else:
                    attachment.install_subscriptions()
            apps_runtime = getattr(self, "apps_runtime", None)
            if apps_runtime is not None:
                apps_runtime.register_source(key)
            mcp_app = mcp.http_app(path="/")
            sse_app = mcp.http_app(path="/", transport="sse")

            # 创建代理应用 - 关键改进：使用代理而不是直接挂载
            mcp_proxy = MCPProxyApp(key, self, "mcp")
            sse_proxy = MCPProxyApp(key, self, "sse")

            # 添加到挂载列表 - 挂载代理应用而不是直接的MCP应用
            self.app_mount_list.append({"path": f"/mcp/{key}", "app": mcp_proxy})
            self.app_mount_list.append({"path": f"/sse/{key}", "app": sse_proxy})

            # 重要：正确管理FastMCP的生命周期
            # FastMCP 应用的 lifespan 必须被父应用管理才能正确初始化
            self.lifespan_tasks[key] = mcp_app.lifespan

            # 存储服务器信息（新增，用于监控）
            self.server_info[key] = {
                "config": value,
                "mcp": mcp,
                "mcp_app": mcp_app,
                "sse_app": sse_app,
                "status": "loaded",
                "auth_status": "none",
            }

            logger.info(f"✓ MCP服务器 {key} 配置成功")
            return True

        except Exception as e:
            logger.error(f"❌ 创建MCP服务器 {key} 失败: {e}")
            if key in self.server_info:
                self._update_server_status(key, "failed", str(e))
            return False

    def mount_all_servers(self, app: FastAPI) -> None:
        """
        将所有服务器挂载到FastAPI应用 - 与原有逻辑完全一致

        Args:
            app: FastAPI应用实例
        """
        # 遍历app_mount_list - 与原逻辑完全一致
        for app_mount in self.app_mount_list:
            print(f"Mounting {app_mount['path']} with {app_mount['app']}")
            app.mount(app_mount["path"], app_mount["app"])

        # 挂载 Catalog 端点
        self._mount_catalog(app)

    def mount_server(self, app: FastAPI, server_name: str) -> bool:
        """只挂载一次本管理器的 MCP/SSE 代理，不覆盖其他来源。"""
        from app.services.mcp_service_mounts import mount_service_routes

        if server_name not in self.server_info:
            return False
        return mount_service_routes(self, app, server_name)

    def _mount_catalog(self, app: FastAPI) -> None:
        """挂载 Catalog MCP 到 /mcp/mcpcat 和 /sse/mcpcat"""
        if not self.catalog_service:
            return

        cat_config = self.catalog_service._config
        if not cat_config.enabled:
            return

        try:
            mcp_app, sse_app = self.catalog_service.create_apps()
            mcp_path = f"/mcp/{cat_config.path_name}"
            sse_path = f"/sse/{cat_config.path_name}"
            app.mount(mcp_path, mcp_app)
            app.mount(sse_path, sse_app)
            logger.info(f"Catalog 端点已挂载: {mcp_path}, {sse_path}")
        except Exception as e:
            logger.warning(f"Catalog 挂载失败: {e}")

    async def _trigger_catalog_refresh(self) -> None:
        """刷新 Catalog 状态统计（安全调用，失败仅日志）。"""
        if not self.catalog_service:
            return
        try:
            await self.catalog_service.refresh_index()
        except Exception as e:
            logger.warning(f"Catalog 目录刷新失败: {e}")

    @service_operation(position=2, keyword="key")
    async def add_and_mount_server(
        self, app: FastAPI, key: str, value: Dict[str, Any]
    ) -> bool:
        """
        添加并动态挂载MCP服务器到运行中的应用

        Args:
            app: FastAPI应用实例
            key: 服务器名称
            value: 服务器配置

        Returns:
            bool: 是否成功添加并挂载
        """
        # 检查服务器是否已存在
        if (
            apps_path_reserved(key)
            or code_mode_path_reserved(key)
            or skills_path_reserved(key)
            or mcp_route_occupied(app, key, owner=self)
        ):
            logger.error("MCP 路径 %s 已被其他端点占用", key)
            return False
        if key in self.lifespan_tasks:
            logger.error(f"服务器 {key} 已存在")
            return False

        # 先添加服务器
        if not self.add_mcp_server(key, value):
            return False

        # 然后动态挂载
        if not self.mount_server(app, key):
            return False

        # 通过 MCP Repository 持久化
        try:
            if ConfigService.add_server_to_config(key, value):
                logger.info("✓ 服务器 %s 配置已保存到 SQLite", key)
            else:
                logger.warning("服务器 %s 配置持久化失败，但内存实例已添加", key)
        except Exception as e:
            logger.warning(f"保存服务器 {key} 配置时出现警告: {e}")

        # 如果应用已经在运行，立即启动这个服务器的生命周期
        code_runtime = getattr(self, "code_mode_runtime", None)
        if code_runtime is not None:
            code_runtime.register_source(key)
        if self.app_started and self.main_app:
            if not await self.lifecycle.start(key, self.main_app):
                return False
            self.lifecycle.schedule(self._detect_auth_status(key, value), key)
            self.lifecycle.schedule(self._trigger_catalog_refresh())
        else:
            self._update_server_status(key, "loaded")

        return True

    @asynccontextmanager
    async def create_unified_lifespan(self, app: FastAPI) -> AsyncIterator[None]:
        """初始和动态来源共用逐服务任务，Catalog 仍由父级上下文持有。"""
        self.app_started, self.main_app = True, app
        self.lifecycle.closing = False
        try:
            async with AsyncExitStack() as stack:
                try:
                    for name in list(self.lifespan_tasks):
                        if await self.lifecycle.start(name, app):
                            config = self.server_info[name].get("config", {})
                            self.lifecycle.schedule(
                                self._detect_auth_status(name, config), name
                            )
                    if self.catalog_service and self.catalog_service.mcp_app:
                        await stack.enter_async_context(
                            self.catalog_service.mcp_app.lifespan(app)
                        )
                        self.catalog_refresh_task = self.lifecycle.schedule(
                            _periodic_catalog_refresh(self)
                        )
                    yield
                finally:
                    self.app_started = False
                    await self.lifecycle.close()
        finally:
            self.app_started, self.main_app = False, None
            self.catalog_refresh_task = None

    async def _detect_auth_status(self, key: str, value: Dict[str, Any]) -> None:
        """异步检测服务器的 OAuth 授权需求，更新 auth_status 字段"""
        server_type = value.get("type", "")
        if server_type not in ("sse", "streamable-http"):
            return

        if (value.get("oauth") or {}).get("grant_type") == "client_credentials":
            from app.services.oauth_machine_probe import detect_machine_auth

            await detect_machine_auth(self.server_info, key, value)
            return

        from app.services.oauth_refresh import refresh_configured_source

        if await refresh_configured_source(self, key, value):
            return

        # 无 token，检测是否需要 OAuth
        url = value.get("url", "")
        if not url:
            return

        from app.models.mcp_config import AuthStatus
        from app.services.oauth_flow import oauth_flow_service
        from app.services.oauth_probe import OAuthProbeError

        original_info = self.server_info.get(key)
        if original_info is None or original_info.get("config") is not value:
            return
        try:
            status, endpoints = await oauth_flow_service.inspect_auth_requirement(
                url, value.get("headers"), server_type
            )
        except OAuthProbeError as error:
            if self.server_info.get(key) is original_info:
                self._update_server_status(key, "failed", str(error))
            return
        if self.server_info.get(key) is not original_info:
            return
        self.server_info[key]["auth_status"] = status.value

        # 如果需要授权，自动发现端点并存入配置
        if status == AuthStatus.AUTH_REQUIRED:
            if endpoints:
                value["oauth"] = {
                    **endpoints,
                    "scopes": endpoints.get("scopes_supported", []),
                    "redirect_mode": "manual",
                    "token": None,
                    **(value.get("oauth") or {}),
                }
                from app.services.config_service import ConfigService

                ConfigService.update_server_oauth(key, value["oauth"])

    def get_server_status(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有服务器状态 - 新增的监控功能

        Returns:
            Dict[str, Dict[str, Any]]: 服务器状态信息
        """
        return {
            name: {
                "status": info.get("status", "unknown"),
                "type": info.get("config", {}).get("type", "unknown"),
                "require_auth": info.get("config", {}).get("require_auth", True),
                "expose_in_catalog": info.get("config", {}).get(
                    "expose_in_catalog", False
                ),
                "error": info.get("error"),
                "note": info.get("config", {}).get("note"),
                "tags": info.get("config", {}).get("tags", []),
                "auth_status": info.get("auth_status", "none"),
                **(
                    {"auth_mode": "client_credentials"}
                    if (info.get("config", {}).get("oauth") or {}).get("grant_type")
                    == "client_credentials"
                    else {}
                ),
                "mcp_endpoint": f"/mcp/{name}",
                "sse_endpoint": f"/sse/{name}",
            }
            for name, info in self.server_info.items()
        }

    def update_server_meta(self, server_name: str, note=None, tags=None) -> bool:
        """
        仅更新服务器的备注/标签元数据，不触发重启。

        同步更新内存中的 server_info 配置与 SQLite，使下次 get_server_status
        立即反映变更。
        """
        if server_name not in self.server_info:
            return False
        from app.services.config_service import ConfigService

        cfg = self.server_info[server_name].get("config")
        if not isinstance(cfg, dict):
            cfg = {}
            self.server_info[server_name]["config"] = cfg
        if note is not None:
            cfg["note"] = note
        if tags is not None:
            cfg["tags"] = tags
        success = ConfigService.update_server_meta(server_name, note=note, tags=tags)
        if success and self.catalog_service:
            try:
                asyncio.get_running_loop()
                self.lifecycle.schedule(self._trigger_catalog_refresh())
            except RuntimeError:
                pass
        return success

    def get_mount_list(self) -> List[Dict[str, Any]]:
        """
        获取挂载列表 - 向后兼容

        Returns:
            List[Dict[str, Any]]: 挂载列表
        """
        return self.app_mount_list.copy()

    def get_lifespan_tasks(self) -> Dict[str, Callable]:
        """
        获取生命周期任务 - 向后兼容

        Returns:
            Dict[str, Callable]: 生命周期任务字典
        """
        return self.lifespan_tasks.copy()

    @service_operation()
    async def stop_server(self, server_name: str) -> bool:
        """停止初始或动态实例，并等待其真实生命周期退出；保留挂载地址。"""
        if server_name not in self.server_info:
            return False
        self._update_server_status(server_name, "stopping")
        runtime = getattr(self, "code_mode_runtime", None)
        if runtime is not None:
            await runtime.cancel_source(server_name)
        if not await self.lifecycle.stop(server_name):
            return False
        self._update_server_status(server_name, "stopped")
        self.lifecycle.schedule(self._trigger_catalog_refresh())
        return True

    @service_operation()
    async def start_server(self, server_name: str) -> bool:
        """等待明确就绪，不会为同一应用重复进入 lifespan。"""
        if server_name not in self.server_info:
            return False
        if not self.app_started or self.main_app is None:
            self._update_server_status(server_name, "loaded")
            return True
        if not await self.lifecycle.start(server_name, self.main_app):
            return False
        config = self.server_info[server_name].get("config", {})
        self.lifecycle.schedule(
            self._detect_auth_status(server_name, config), server_name
        )
        self.lifecycle.schedule(self._trigger_catalog_refresh())
        return True

    @service_operation()
    async def restart_server(
        self, server_name: str, new_config: dict | None = None
    ) -> bool:
        """候选验证后停止旧代际，再持久化并启动替换代际。"""
        from app.services.mcp_service_restart import restart_source

        return await restart_source(self, server_name, new_config)

    @service_operation()
    async def remove_server(self, server_name: str) -> bool:
        """生命周期关闭且持久化删除成功后移除来源，失败可重试。"""
        if server_name not in self.server_info:
            logger.warning(f"服务器 {server_name} 不存在，跳过移除")
            return True

        try:
            logger.info(f"开始移除服务器 {server_name}")

            # 1. 停止服务
            if not await self.stop_server(server_name):
                return False

            # 先完成持久化删除，失败时保留已停止的内存记录以便重试。
            from app.services.skill_artifact_service import mark_mcp_source_missing

            if ConfigService.get_server_config(server_name) is not None:
                if not ConfigService.remove_server_from_config(server_name):
                    return False
            mark_mcp_source_missing(ConfigService._require_database(), server_name)

            # 2. 清理内存数据
            if server_name in self.server_info:
                del self.server_info[server_name]
            if server_name in self.lifespan_tasks:
                del self.lifespan_tasks[server_name]

            # 4. 清理挂载列表（移除代理应用引用）
            self.app_mount_list = [
                mount
                for mount in self.app_mount_list
                if not mount["path"].endswith(f"/{server_name}")
            ]

            # 注意：FastAPI不支持动态卸载路由，所以代理应用仍然存在
            # 但由于server_info已被删除，代理会返回404错误

            logger.info(f"✓ 服务器 {server_name} 移除成功")
            self.lifecycle.schedule(self._trigger_catalog_refresh())
            return True

        except Exception as e:
            logger.error(f"移除服务器 {server_name} 失败: {e}")
            return False


async def _periodic_catalog_refresh(manager: "MCPServerManager") -> None:
    """定期更新 Catalog 状态统计，工具目录本身由 Provider 实时提供。"""
    await asyncio.sleep(10)
    while manager.catalog_service:
        await manager._trigger_catalog_refresh()
        interval = manager.catalog_service._config.refresh_interval_sec
        await asyncio.sleep(interval)


# 模块级单例
server_manager = MCPServerManager()
