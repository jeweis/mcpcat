"""mcpcat FastAPI 应用工厂。"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    admin,
    apps,
    apps_mcp_settings,
    auth,
    catalog,
    code_mode_settings,
    health,
    inspector,
    market,
    oauth,
    servers,
    skill_generation,
    skill_variants,
    skills,
    skills_mcp_settings,
)
from app.bootstrap import bootstrap_storage
from app.core.config import settings
from app.core.mcp_runtime import configure_mcp_runtime
from app.core.oauth_logging import install_oauth_access_filter
from app.middleware.auth import AuthMiddleware
from app.middleware.mcp_private_cache import MCPPrivateCacheMiddleware
from app.models.mcp_config import CatalogConfig
from app.services.apps_mcp_runtime import AppsMCPRuntime
from app.services.apps_mcp_settings import apps_mcp_config, validate_apps_path
from app.services.catalog_service import CatalogService
from app.services.code_mode_runtime import CodeModeRuntime
from app.services.code_mode_settings import (
    code_mode_config,
    validate_code_mode_dependencies,
    validate_code_mode_path,
)
from app.services.config_service import ConfigService
from app.services.inspector_service import inspector_service
from app.services.market_service import (
    MARKET_DATA_TTL,
    MARKET_DATA_URL_FALLBACK,
    MARKET_DATA_URL_PRIMARY,
    MarketService,
)
from app.services.platform_endpoints import prepare_platform_paths
from app.services.security_service import security_service
from app.services.server_manager import MCPServerManager
from app.services.skills_mcp_runtime import SkillsMCPRuntime
from app.storage.database import Database
from app.version import APP_VERSION

logger = logging.getLogger(__name__)


def create_app(
    *,
    database: Optional[Database] = None,
    server_manager: Optional[MCPServerManager] = None,
) -> FastAPI:
    """完成存储 Bootstrap 后创建业务服务与 FastAPI 应用。"""

    configure_mcp_runtime()
    install_oauth_access_filter()
    bootstrap = bootstrap_storage(database)
    manager = server_manager or MCPServerManager()
    prepare_platform_paths(manager)
    validate_apps_path(apps_mcp_config(), manager)

    created_keys = security_service.ensure_default_keys()
    if created_keys:
        logger.warning("首次运行已创建 %d 个默认 API Key", len(created_keys))

    skills_runtime = SkillsMCPRuntime(bootstrap.database, manager)
    manager.skills_runtime = skills_runtime
    code_config = code_mode_config()
    validate_code_mode_path(code_config, manager)
    validate_code_mode_dependencies()
    manager.load_servers_from_config()
    catalog_config = CatalogConfig(
        **(ConfigService.get_setting_section("catalog", {}) or {})
    )
    if catalog_config.enabled:
        manager.catalog_service = CatalogService(manager, catalog_config)
    code_runtime = CodeModeRuntime(bootstrap.database, manager)
    manager.code_mode_runtime = code_runtime
    apps_runtime = AppsMCPRuntime(bootstrap.database, manager)
    manager.apps_runtime = apps_runtime

    market_service = MarketService(
        remote_url=MARKET_DATA_URL_PRIMARY,
        remote_url_fallback=MARKET_DATA_URL_FALLBACK,
        ttl_seconds=MARKET_DATA_TTL,
        local_path=Path(__file__).resolve().parents[1] / "data" / "mcp_market.json",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        inspector_service.start_cleanup_task()
        market_service.refresh_async()
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(manager.create_unified_lifespan(app))
            await stack.enter_async_context(skills_runtime.lifespan())
            await stack.enter_async_context(code_runtime.lifespan())
            await stack.enter_async_context(apps_runtime.lifespan())
            yield
        bootstrap.database.dispose()

    application = FastAPI(
        title=settings.app_name,
        description=settings.description,
        version=APP_VERSION,
        lifespan=lifespan,
    )
    application.state.storage_ready = True
    application.state.database = bootstrap.database
    application.state.schema_status = bootstrap.schema
    application.state.legacy_migration = bootstrap.legacy
    application.state.server_manager = manager
    application.state.port = settings.port
    application.state.market_service = market_service

    application.add_middleware(AuthMiddleware)
    application.add_middleware(MCPPrivateCacheMiddleware)
    manager.mount_all_servers(application)
    skills_runtime.mount(application)
    code_runtime.mount(application)
    apps_runtime.mount(application)
    application.include_router(health.router, prefix="/api", tags=["健康检查"])
    application.include_router(servers.router, prefix="/api", tags=["服务器管理"])
    application.include_router(auth.router, prefix="/api", tags=["认证"])
    application.include_router(
        inspector.router, prefix="/api/inspector", tags=["测试工具"]
    )
    application.include_router(market.router, prefix="/api/market", tags=["发现市场"])
    application.include_router(admin.router, prefix="/api", tags=["全局设置"])
    application.include_router(
        skills_mcp_settings.router, prefix="/api", tags=["远程 Skills 设置"]
    )
    application.include_router(
        code_mode_settings.router, prefix="/api", tags=["CodeMode 设置"]
    )
    application.include_router(oauth.router, prefix="/api", tags=["OAuth 认证"])
    application.include_router(catalog.router, prefix="/api", tags=["Catalog 搜索"])
    application.include_router(apps.router, prefix="/api", tags=["Apps 管理"])
    application.include_router(
        apps_mcp_settings.router, prefix="/api", tags=["Apps 远程设置"]
    )
    application.include_router(
        skill_generation.router, prefix="/api", tags=["Skills 模式生成"]
    )
    application.include_router(skills.router, prefix="/api", tags=["Skills 管理"])
    application.include_router(
        skill_variants.router, prefix="/api", tags=["Skills 内容变体"]
    )

    static_dir = Path(__file__).resolve().parents[1] / "static"
    if static_dir.exists():
        application.mount("/static", StaticFiles(directory=static_dir), name="static")
        application.mount(
            "/ui", StaticFiles(directory=static_dir, html=True), name="ui"
        )

    @application.get("/")
    async def root():
        if static_dir.exists():
            return RedirectResponse(url="/ui/")
        return {"message": f"Welcome to {settings.app_name} - {settings.description}"}

    return application
