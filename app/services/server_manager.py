"""服务器管理器 - 封装MCP服务器管理逻辑"""

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, Callable, Dict, List, Optional, Set

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.services.config_service import ConfigService
from app.services.mcp_factory import MCPServerFactory

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


class MCPProxyApp:
    """
    MCP服务器代理应用 - 允许动态切换底层MCP应用实例

    这个代理应用被FastAPI挂载，内部可以动态转发请求到不同的MCP应用实例，
    从而实现配置热重载而无需重启整个应用。
    """

    def __init__(
        self,
        server_name: str,
        server_manager: "MCPServerManager",
        transport_type: str = "mcp",
    ):
        """
        初始化代理应用

        Args:
            server_name: 服务器名称
            server_manager: 服务器管理器实例
            transport_type: 传输类型 ('mcp' 或 'sse')
        """
        self.server_name = server_name
        self.server_manager = server_manager
        self.transport_type = transport_type

    async def __call__(self, scope, receive, send):
        """
        ASGI应用接口 - 处理所有传入的请求

        Args:
            scope: ASGI scope
            receive: ASGI receive callable
            send: ASGI send callable
        """
        try:
            # 获取当前的服务器信息
            server_info = self.server_manager.server_info.get(self.server_name)

            if not server_info:
                # 服务器不存在
                await self._send_error_response(
                    scope,
                    receive,
                    send,
                    status_code=404,
                    message=f"MCP服务器 '{self.server_name}' 不存在",
                )
                return

            # 检查服务器状态
            server_status = server_info.get("status", "unknown")
            if server_status != "running":
                # 服务器未运行
                await self._send_error_response(
                    scope,
                    receive,
                    send,
                    status_code=503,
                    message=f"MCP服务器 '{self.server_name}' 当前不可用 (状态: {server_status})",
                )
                return

            # 获取目标应用实例
            if self.transport_type == "mcp":
                target_app = server_info.get("mcp_app")
            elif self.transport_type == "sse":
                target_app = server_info.get("sse_app")
            else:
                await self._send_error_response(
                    scope,
                    receive,
                    send,
                    status_code=500,
                    message=f"不支持的传输类型: {self.transport_type}",
                )
                return

            if not target_app:
                # 目标应用不可用
                message = (
                    f"MCP服务器 '{self.server_name}' 的 "
                    f"{self.transport_type} 应用不可用"
                )
                await self._send_error_response(
                    scope,
                    receive,
                    send,
                    status_code=503,
                    message=message,
                )
                return

            # 转发请求到目标应用
            await target_app(scope, receive, send)

        except Exception as e:
            # 处理代理层的异常
            logger.error(f"MCP代理应用 {self.server_name} 处理请求时出错: {e}")
            try:
                await self._send_error_response(
                    scope,
                    receive,
                    send,
                    status_code=500,
                    message=f"代理服务器内部错误: {str(e)}",
                )
            except:
                # 如果连错误响应都发送失败，只能记录日志
                logger.error(f"发送错误响应失败: {e}")

    async def _send_error_response(
        self, scope, receive, send, status_code: int, message: str
    ):
        """
        发送错误响应

        Args:
            scope: ASGI scope
            receive: ASGI receive callable
            send: ASGI send callable
            status_code: HTTP状态码
            message: 错误消息
        """
        if scope["type"] == "http":
            # HTTP请求，返回JSON错误响应
            response = JSONResponse(
                content={"error": message, "server": self.server_name},
                status_code=status_code,
            )
            await response(scope, receive, send)
        else:
            # 其他类型的请求（如WebSocket），发送基本的错误响应
            await send(
                {
                    "type": "http.response.start",
                    "status": status_code,
                    "headers": [[b"content-type", b"text/plain"]],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": message.encode("utf-8"),
                }
            )


class MCPServerManager:
    """MCP服务器管理器 - 封装现有的服务器管理逻辑"""

    def __init__(self):
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

        print("Loading MCP server list...")
        print("MCP server list loaded.")
        print(mcp_server_list)

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

            # 创建应用并正确获取生命周期
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
        """
        动态挂载单个服务器到运行中的FastAPI应用

        Args:
            app: FastAPI应用实例
            server_name: 服务器名称

        Returns:
            bool: 是否成功挂载
        """
        if server_name not in self.server_info:
            logger.error(f"服务器 {server_name} 不存在")
            return False

        try:
            # 为动态添加的服务器创建代理应用
            mcp_proxy = MCPProxyApp(server_name, self, "mcp")
            sse_proxy = MCPProxyApp(server_name, self, "sse")

            # 动态挂载代理应用
            mcp_path = f"/mcp/{server_name}"
            sse_path = f"/sse/{server_name}"

            try:
                print(f"动态挂载 {mcp_path} 到应用")
                app.mount(mcp_path, mcp_proxy)
                logger.info(f"✓ 成功挂载 {mcp_path}")

                print(f"动态挂载 {sse_path} 到应用")
                app.mount(sse_path, sse_proxy)
                logger.info(f"✓ 成功挂载 {sse_path}")

                # 更新挂载列表
                self.app_mount_list.append({"path": mcp_path, "app": mcp_proxy})
                self.app_mount_list.append({"path": sse_path, "app": sse_proxy})

            except Exception as mount_error:
                # 如果挂载失败（可能是路径冲突），记录但继续
                logger.warning(f"挂载时出现警告: {mount_error}")

            # 更新服务器状态
            self._update_server_status(server_name, "mounted")
            return True

        except Exception as e:
            logger.error(f"挂载服务器 {server_name} 失败: {e}")
            self._update_server_status(server_name, "mount_failed", str(e))
            return False

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

    async def _run_dynamic_server_lifespan(self, server_name: str, app: FastAPI):
        """
        运行动态服务器的生命周期作为独立任务

        Args:
            server_name: 服务器名称
            app: FastAPI应用实例
        """
        try:
            print(f"🚀 启动动态服务器 {server_name} 的生命周期")

            # 获取生命周期任务
            task_lifespan = self.lifespan_tasks[server_name]

            # 运行生命周期
            async with task_lifespan(app):
                print(f"✓ 动态服务器 {server_name} 生命周期启动成功")
                self._update_server_status(server_name, "running")

                # 等待任务被取消
                try:
                    await asyncio.Event().wait()  # 无限等待直到被取消
                except asyncio.CancelledError:
                    print(f"🔄 动态服务器 {server_name} 生命周期正在关闭")
                    raise  # 重新抛出，让上下文管理器正常退出

        except asyncio.CancelledError:
            print(f"✓ 动态服务器 {server_name} 生命周期已关闭")
            self._update_server_status(server_name, "stopped")
        except Exception as e:
            print(f"✗ 动态服务器 {server_name} 生命周期出错: {e}")
            logger.error(f"动态服务器 {server_name} 生命周期出错: {e}")
            self._update_server_status(server_name, "failed", str(e))

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
        if key in self.lifespan_tasks:
            logger.error(f"服务器 {key} 已存在")
            return False

        # 先添加服务器
        if not self.add_mcp_server(key, value):
            return False

        # 然后动态挂载
        if not self.mount_server(app, key):
            return False

        # 保存配置到文件，确保持久化
        try:
            if ConfigService.add_server_to_config(key, value):
                print(f"✓ 服务器 {key} 配置已保存到文件")
            else:
                print(f"⚠️  服务器 {key} 配置保存失败，但服务器已添加")
        except Exception as e:
            logger.warning(f"保存服务器 {key} 配置时出现警告: {e}")

        # 如果应用已经在运行，立即启动这个服务器的生命周期
        if self.app_started and self.main_app:
            try:
                # 创建独立的后台任务来运行动态服务器的生命周期
                task = asyncio.create_task(
                    self._run_dynamic_server_lifespan(key, self.main_app)
                )
                # 为任务添加服务器名称标识
                task._server_name = key
                self.dynamic_tasks.add(task)

                # 添加回调来清理完成的任务
                task.add_done_callback(self.dynamic_tasks.discard)

                # 等待一小段时间确保服务器启动完成
                await asyncio.sleep(0.1)

                # 检查服务器是否成功启动
                if self.server_info[key]["status"] == "running":
                    print(f"✅ 动态服务器 {key} 已挂载并启动，完整功能立即可用")
                else:
                    print(f"⚠️  动态服务器 {key} 已挂载，生命周期启动中...")

                # 异步检测 OAuth 授权需求（不阻塞添加流程）
                asyncio.create_task(self._detect_auth_status(key, value))

                # 更新 Catalog 状态统计；搜索目录由动态 Provider 实时读取
                asyncio.create_task(self._trigger_catalog_refresh())

            except Exception as e:
                print(f"✗ 动态服务器 {key} 启动失败: {e}")
                logger.error(f"启动服务器 {key} 失败: {e}")

                # 清理已添加的服务器
                if key in self.lifespan_tasks:
                    del self.lifespan_tasks[key]
                self._update_server_status(key, "failed", str(e))

                return False
        else:
            # 如果应用还没启动，标记为已加载
            self._update_server_status(key, "loaded")

        return True

    @asynccontextmanager
    async def create_unified_lifespan(self, app: FastAPI):
        """
        正确的FastMCP生命周期管理器

        FastMCP应用的lifespan必须由父应用统一管理，
        这样StreamableHTTPSessionManager才能正确初始化

        Args:
            app: FastAPI应用实例
        """
        print("应用启动中...")

        # 设置应用已启动状态和保存应用实例
        self.app_started = True
        self.main_app = app

        # 使用 AsyncExitStack 来正确管理所有的 lifespan 上下文
        async with AsyncExitStack() as stack:
            # 启动所有FastMCP服务器的生命周期
            for task_name, task_lifespan in self.lifespan_tasks.items():
                try:
                    # 每个FastMCP应用的lifespan必须在这里被正确管理
                    await stack.enter_async_context(task_lifespan(app))
                    print(f"✓ MCP服务器 {task_name} 生命周期启动成功")

                    # 更新服务器状态
                    self._update_server_status(task_name, "running")

                    # 异步检测 OAuth 授权需求（不阻塞启动）
                    cfg = self.server_info.get(task_name, {}).get("config", {})
                    if isinstance(cfg, dict) and cfg.get("type") in (
                        "sse",
                        "streamable-http",
                    ):
                        asyncio.create_task(self._detect_auth_status(task_name, cfg))

                except Exception as e:
                    print(f"✗ MCP服务器 {task_name} 生命周期启动失败: {e}")

                    # 更新服务器状态
                    self._update_server_status(task_name, "failed", str(e))

            try:
                # 启动 Catalog 生命周期并定期更新目录状态统计
                if self.catalog_service and self.catalog_service.mcp_app:
                    catalog_lifespan = self.catalog_service.mcp_app.lifespan
                    await stack.enter_async_context(catalog_lifespan(app))
                    self.catalog_refresh_task = asyncio.create_task(
                        _periodic_catalog_refresh(self)
                    )

                yield
            finally:
                print("应用关闭中...")

                if self.catalog_refresh_task:
                    self.catalog_refresh_task.cancel()
                    await asyncio.gather(
                        self.catalog_refresh_task,
                        return_exceptions=True,
                    )
                    self.catalog_refresh_task = None

                # 取消所有动态服务器任务
                if self.dynamic_tasks:
                    print(f"正在关闭 {len(self.dynamic_tasks)} 个动态服务器...")
                    for task in self.dynamic_tasks:
                        if not task.done():
                            task.cancel()

                    # 等待所有任务完成，给更多时间
                    if self.dynamic_tasks:
                        try:
                            await asyncio.wait_for(
                                asyncio.gather(
                                    *self.dynamic_tasks, return_exceptions=True
                                ),
                                timeout=5.0,  # 给5秒时间优雅关闭
                            )
                            print("✓ 所有动态服务器已关闭")
                        except asyncio.TimeoutError:
                            print("⚠️  部分动态服务器关闭超时，强制终止")

                # 给底层连接一些时间完成
                await asyncio.sleep(0.5)

                # AsyncExitStack 会自动按相反顺序调用所有的 __aexit__

                # 更新所有服务器状态为已停止
                for task_name in self.lifespan_tasks.keys():
                    self._update_server_status(task_name, "stopped")

                # 清理状态
                self.app_started = False
                self.main_app = None
                self.dynamic_tasks.clear()

    async def _detect_auth_status(self, key: str, value: Dict[str, Any]) -> None:
        """异步检测服务器的 OAuth 授权需求，更新 auth_status 字段"""
        server_type = value.get("type", "")
        if server_type not in ("sse", "streamable-http"):
            return

        # 如果已有 OAuth token，检查是否有效
        oauth_config = value.get("oauth")
        if oauth_config and oauth_config.get("token"):
            from app.models.mcp_config import OAuthToken
            from app.services.oauth_flow import oauth_flow_service

            token = OAuthToken(**oauth_config["token"])
            if oauth_flow_service.is_token_expired(token):
                # 尝试刷新
                from app.models.mcp_config import OAuthConfig

                cfg = OAuthConfig(**oauth_config)
                new_token = await oauth_flow_service.refresh_oauth_token(key, cfg)
                if new_token:
                    cfg.token = new_token
                    value["oauth"] = cfg.dict()
                    self.server_info[key]["auth_status"] = "authorized"
                    from app.services.config_service import ConfigService

                    ConfigService.update_server_oauth(key, cfg)
                else:
                    self.server_info[key]["auth_status"] = "auth_expired"
            else:
                self.server_info[key]["auth_status"] = "authorized"
            return

        # 无 token，检测是否需要 OAuth
        url = value.get("url", "")
        if not url:
            return

        from app.models.mcp_config import AuthStatus
        from app.services.oauth_flow import oauth_flow_service

        status = await oauth_flow_service.detect_auth_requirement(url)
        self.server_info[key]["auth_status"] = status.value

        # 如果需要授权，自动发现端点并存入配置
        if status == AuthStatus.AUTH_REQUIRED:
            endpoints = await oauth_flow_service.discover_endpoints(url)
            if endpoints:
                value["oauth"] = {
                    "authorization_endpoint": endpoints.get("authorization_endpoint"),
                    "token_endpoint": endpoints.get("token_endpoint"),
                    "registration_endpoint": endpoints.get("registration_endpoint"),
                    "scopes": endpoints.get("scopes_supported", []),
                    "redirect_mode": "manual",
                    "token": None,
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
                "mcp_endpoint": f"/mcp/{name}",
                "sse_endpoint": f"/sse/{name}",
            }
            for name, info in self.server_info.items()
        }

    def update_server_meta(self, server_name: str, note=None, tags=None) -> bool:
        """
        仅更新服务器的备注/标签元数据，不触发重启。

        同步更新内存中的 server_info 配置与 config.json，使下次 get_server_status
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
                asyncio.get_running_loop().create_task(self._trigger_catalog_refresh())
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

    async def stop_server(self, server_name: str) -> bool:
        """
        停止指定服务器（保持路由挂载）

        Args:
            server_name: 服务器名称

        Returns:
            bool: 是否成功停止
        """
        if server_name not in self.server_info:
            logger.error(f"服务器 {server_name} 不存在")
            return False

        try:
            # 查找并取消对应的动态任务
            task_to_cancel = None
            for task in self.dynamic_tasks:
                if hasattr(task, "_server_name") and task._server_name == server_name:
                    task_to_cancel = task
                    break

            if task_to_cancel and not task_to_cancel.done():
                task_to_cancel.cancel()
                try:
                    await asyncio.wait_for(task_to_cancel, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                self.dynamic_tasks.discard(task_to_cancel)

            # 更新服务器状态
            self._update_server_status(server_name, "stopped")
            logger.info(f"✓ 服务器 {server_name} 停止成功")
            asyncio.create_task(self._trigger_catalog_refresh())
            return True

        except Exception as e:
            logger.error(f"停止服务器 {server_name} 失败: {e}")
            self._update_server_status(server_name, "failed", str(e))
            return False

    async def start_server(self, server_name: str) -> bool:
        """
        启动指定服务器

        Args:
            server_name: 服务器名称

        Returns:
            bool: 是否成功启动
        """
        if server_name not in self.server_info:
            logger.error(f"服务器 {server_name} 不存在")
            return False

        try:
            # 检查服务器是否已经在运行
            if self.server_info[server_name]["status"] == "running":
                logger.info(f"服务器 {server_name} 已经在运行")
                return True

            # 如果应用已经启动，启动服务器的生命周期
            if self.app_started and self.main_app:
                task = asyncio.create_task(
                    self._run_dynamic_server_lifespan(server_name, self.main_app)
                )
                # 为任务添加服务器名称标识
                task._server_name = server_name
                self.dynamic_tasks.add(task)
                task.add_done_callback(self.dynamic_tasks.discard)

                # 等待一小段时间确保服务器启动
                await asyncio.sleep(0.1)

                logger.info(f"✓ 服务器 {server_name} 启动成功")
                asyncio.create_task(self._trigger_catalog_refresh())
                return True
            else:
                # 如果应用还没启动，只更新状态
                self._update_server_status(server_name, "loaded")
                return True

        except Exception as e:
            logger.error(f"启动服务器 {server_name} 失败: {e}")
            self._update_server_status(server_name, "failed", str(e))
            return False

    async def restart_server(self, server_name: str, new_config: dict = None) -> bool:
        """
        重启服务器，可选择更新配置

        Args:
            server_name: 服务器名称
            new_config: 新的配置（可选）

        Returns:
            bool: 是否成功重启
        """
        if server_name not in self.server_info:
            logger.error(f"服务器 {server_name} 不存在")
            return False

        try:
            logger.info(f"开始重启服务器 {server_name}")
            self._update_server_status(server_name, "restarting")

            # 1. 停止当前服务
            await self.stop_server(server_name)

            # 等待一小段时间确保旧的生命周期完全关闭
            await asyncio.sleep(0.2)

            # 2. 更新配置（如果提供）
            if new_config:
                # 验证新配置
                from app.services.config_service import ConfigService

                is_valid, error_msg = ConfigService.validate_server_config(new_config)
                if not is_valid:
                    logger.error(f"新配置验证失败: {error_msg}")
                    self._update_server_status(
                        server_name, "failed", f"配置验证失败: {error_msg}"
                    )
                    return False

                # 更新配置文件
                if not ConfigService.update_server_config(server_name, new_config):
                    logger.error("更新配置文件失败")
                    self._update_server_status(
                        server_name, "failed", "更新配置文件失败"
                    )
                    return False

                # 更新内存中的配置
                self.server_info[server_name]["config"] = new_config

            # 3. 重新创建服务器实例
            config = new_config or self.server_info[server_name]["config"]
            mcp = MCPServerFactory.create_server(server_name, config)
            if not mcp:
                logger.error("重新创建MCP服务器实例失败")
                self._update_server_status(server_name, "failed", "创建服务器实例失败")
                return False

            # 4. 更新服务器信息
            self.server_info[server_name]["mcp"] = mcp
            mcp_app = mcp.http_app(path="/")
            sse_app = mcp.http_app(path="/", transport="sse")
            self.server_info[server_name]["mcp_app"] = mcp_app
            self.server_info[server_name]["sse_app"] = sse_app

            # 5. 重要：更新生命周期任务为新的MCP实例的生命周期
            self.lifespan_tasks[server_name] = mcp_app.lifespan

            # 注意：不需要更新挂载列表，因为代理应用会自动使用server_info中的新应用实例

            # 7. 启动新的服务
            success = await self.start_server(server_name)

            if success:
                logger.info(f"✓ 服务器 {server_name} 重启成功")
                asyncio.create_task(self._trigger_catalog_refresh())
                return True
            else:
                logger.error(f"重启后启动服务器 {server_name} 失败")
                return False

        except Exception as e:
            logger.error(f"重启服务器 {server_name} 失败: {e}")
            self._update_server_status(server_name, "failed", str(e))
            return False

    async def remove_server(self, server_name: str) -> bool:
        """
        完全移除服务器

        Args:
            server_name: 服务器名称

        Returns:
            bool: 是否成功移除
        """
        if server_name not in self.server_info:
            logger.warning(f"服务器 {server_name} 不存在，跳过移除")
            return True

        try:
            logger.info(f"开始移除服务器 {server_name}")

            # 1. 停止服务
            await self.stop_server(server_name)

            # 2. 清理内存数据
            if server_name in self.server_info:
                del self.server_info[server_name]
            if server_name in self.lifespan_tasks:
                del self.lifespan_tasks[server_name]

            # 3. 从配置文件中移除
            from app.services.config_service import ConfigService

            ConfigService.remove_server_from_config(server_name)

            # 4. 清理挂载列表（移除代理应用引用）
            self.app_mount_list = [
                mount
                for mount in self.app_mount_list
                if not mount["path"].endswith(f"/{server_name}")
            ]

            # 注意：FastAPI不支持动态卸载路由，所以代理应用仍然存在
            # 但由于server_info已被删除，代理会返回404错误

            logger.info(f"✓ 服务器 {server_name} 移除成功")
            asyncio.create_task(self._trigger_catalog_refresh())
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
