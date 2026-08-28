"""mcpcat FastAPI 应用工厂。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    admin,
    auth,
    catalog,
    health,
    inspector,
    market,
    oauth,
    servers,
    skills,
)
from app.bootstrap import bootstrap_storage
from app.core.config import settings
from app.middleware.auth import AuthMiddleware
from app.models.mcp_config import CatalogConfig
from app.services.catalog_service import CatalogService
from app.services.config_service import ConfigService
from app.services.inspector_service import inspector_service
from app.services.market_service import (
    MARKET_DATA_TTL,
    MARKET_DATA_URL_FALLBACK,
    MARKET_DATA_URL_PRIMARY,
    MarketService,
)
from app.services.security_service import security_service
from app.services.server_manager import MCPServerManager
from app.storage.database import Database

logger = logging.getLogger(__name__)


def create_app(
    *,
    database: Optional[Database] = None,
    server_manager: Optional[MCPServerManager] = None,
) -> FastAPI:
    """完成存储 Bootstrap 后创建业务服务与 FastAPI 应用。"""

    bootstrap = bootstrap_storage(database)
    manager = server_manager or MCPServerManager()

    created_keys = security_service.ensure_default_keys()
    if created_keys:
        logger.warning("首次运行已创建 %d 个默认 API Key", len(created_keys))

    manager.load_servers_from_config()
    catalog_config = CatalogConfig(
        **(ConfigService.get_setting_section("catalog", {}) or {})
    )
    if catalog_config.enabled:
        manager.catalog_service = CatalogService(manager, catalog_config)

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
        async with manager.create_unified_lifespan(app):
            yield
        bootstrap.database.dispose()

    application = FastAPI(
        title=settings.app_name,
        description=settings.description,
        version=settings.app_version,
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
    manager.mount_all_servers(application)
    application.include_router(health.router, prefix="/api", tags=["健康检查"])
    application.include_router(servers.router, prefix="/api", tags=["服务器管理"])
    application.include_router(auth.router, prefix="/api", tags=["认证"])
    application.include_router(
        inspector.router, prefix="/api/inspector", tags=["测试工具"]
    )
    application.include_router(market.router, prefix="/api/market", tags=["发现市场"])
    application.include_router(admin.router, prefix="/api", tags=["全局设置"])
    application.include_router(oauth.router, prefix="/api", tags=["OAuth 认证"])
    application.include_router(catalog.router, prefix="/api", tags=["Catalog 搜索"])
    if settings.mcpcat_skills_enabled:
        application.include_router(skills.router, prefix="/api", tags=["Skills 管理"])

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
