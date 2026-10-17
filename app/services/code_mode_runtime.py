"""独立 CodeMode HTTP 端点，保留 Catalog/Skills/旧 MCP 路由。"""

import hashlib
import hmac
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.experimental.transforms.code_mode import CodeMode
from fastmcp.server.dependencies import get_http_request
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app.models.mcp_config import PermissionType
from app.services.code_mode_execution import EXECUTION_SCOPE, SourceGeneration
from app.services.code_mode_leases import ExecutionLeasePool
from app.services.code_mode_marker import CodeModeMarker
from app.services.code_mode_provider import CodeModeProvider
from app.services.code_mode_sandbox import CodeModeSandboxProvider
from app.services.code_mode_settings import code_mode_config, validate_code_mode_path
from app.services.config_service import ConfigService
from app.services.security_service import security_service
from app.storage.database import Database
from app.storage.runtime_identities import (
    current_endpoint_identity,
    endpoint_identity,
    ensure_runtime_identity,
)


class CodeModeRuntime:
    """固定挂载独立入口，由父应用管理生命周期与受限执行。"""

    def __init__(self, database: Database, manager: Any) -> None:
        self.database, self.manager = database, manager
        self.config = code_mode_config()
        self.alive = False
        self.http_app = None
        self.server = None
        self.bridge = None
        self._salt = None
        validate_code_mode_path(self.config, manager)
        self._salt = ensure_runtime_identity(
            database, "code-mode:principal-salt"
        ).encode()
        for name in manager.server_info:
            self.register_source(name)
        directory = CodeModeProvider(
            manager,
            authorize=self.authorize,
            dispatch_guard=self.dispatch_guard,
            lookup_allowed=self.lookup_allowed,
        )
        self.bridge = CodeModeSandboxProvider(
            directory=directory,
            config_loader=code_mode_config,
            principal=self.principal,
            sources=self.sources,
            leases=ExecutionLeasePool(database.path),
        )
        self.server = FastMCP(
            "mcpcat CodeMode",
            providers=[directory],
            transforms=[
                CodeMode(sandbox_provider=self.bridge, max_tool_calls=1000),
                CodeModeMarker(),
            ],
        )
        # 固定 SDK 的 JSON-only 路径不监听断连；使用原生流式路径使
        # 现代客户端关闭请求及 legacy cancellation 都能到达执行任务。
        self.http_app = self.server.http_app(path="/")

    def available(self) -> bool:
        """生命周期结束或改路径立即失效；保存配置不伪造就绪。"""
        try:
            current = code_mode_config()
            return (
                self.alive
                and self.http_app is not None
                and current.path_name == self.config.path_name
            )
        except ValueError:
            return False

    def _verify(self, key: str) -> str | None:
        validated = security_service.verify_api_key(key)
        if not validated or not security_service.has_permission(
            validated, PermissionType.READ
        ):
            return None
        return hmac.new(self._salt, validated.key.encode(), hashlib.sha256).hexdigest()

    def principal(self) -> str | None:
        """身份来自当前 HTTP Key，不相信 request.state 或客户端 metadata。"""
        if not self.available():
            return None
        try:
            request = get_http_request()
        except RuntimeError:
            return None
        return self._verify(
            request.headers.get(security_service.get_auth_header_name(), "")
        )

    def authorize(self) -> bool:
        """发现及每次内部 lookup 都重新验证 READ。"""
        return self.principal() is not None

    def dispatch_guard(self, tool: Any) -> bool:
        """只有 execute 内的受控 callback 能分发业务工具。"""
        scope = EXECUTION_SCOPE.get()
        return scope is not None and self.authorize() and scope.permits_source(tool)

    def lookup_allowed(self) -> bool:
        """直接猜业务工具名时，在查询上游描述之前就拒绝。"""
        scope = EXECUTION_SCOPE.get()
        if scope is None or not self.authorize():
            return False
        scope.check()
        return True

    def register_source(self, name: str) -> None:
        """只在明确配置/注册来源时创建身份，请求不会恢复退休身份。"""
        if ConfigService.get_server_config(name) is not None:
            endpoint_identity(self.database, "service", name)

    def sources(self) -> dict[str, SourceGeneration]:
        """来源读取只用当前持久身份及运行对象，重建后不能沿用。"""
        result = {}
        for name, info in self.manager.server_info.items():
            identity = current_endpoint_identity(self.database, "service", name)
            if (
                identity is not None
                and info.get("status") == "running"
                and info.get("mcp") is not None
            ):
                result[name] = SourceGeneration(identity, info["mcp"])
        return result

    def mount(self, app: FastAPI) -> None:
        """独立系统路由不写入用户 MCP 配置或服务器列表。"""
        app.state.code_mode_runtime = self
        if self.http_app is not None:
            app.mount(f"/mcp/{self.config.path_name}", self)

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """退出时先停接收，再取消编排；不重试或声称上游回滚。"""
        if self.http_app is None:
            yield
            return
        async with self.http_app.lifespan(self.http_app):
            self.alive = True
            try:
                yield
            finally:
                self.alive = False
                await self.bridge.cancel_all()

    async def cancel_executions(self) -> None:
        """安全配置改变时取消现有编排。"""
        if self.bridge is not None:
            await self.bridge.cancel_all()

    async def cancel_source(self, name: str) -> None:
        """来源关闭/替换前停止依赖该来源的现有编排。"""
        if self.bridge is not None:
            await self.bridge.cancel_source(name)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI 层也强制 READ，不能被同名旧 Catalog 的公开设置绕过。"""
        if not self.available():
            await JSONResponse(
                {"detail": "CodeMode endpoint unavailable"}, status_code=404
            )(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if (
            self._verify(headers.get(security_service.get_auth_header_name(), ""))
            is None
        ):
            await JSONResponse({"detail": "READ Key required"}, status_code=401)(
                scope, receive, send
            )
            return
        await self.http_app(scope, receive, send)
