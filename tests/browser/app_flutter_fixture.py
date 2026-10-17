"""临时完整 Web 联调：独立 SQLite/配置，仅绑定 loopback，不访问用户数据。"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.storage.database import Database


class EmptyManager:
    """夹具不启动任何用户 MCP 或子进程。"""

    server_info = {}
    catalog_service = None

    def load_servers_from_config(self) -> None:
        """保持服务目录为空。"""

    def mount_all_servers(self, app: FastAPI) -> None:
        """不挂载用户服务。"""

    def get_server_status(self) -> dict:
        """仪表盘只见空目录，不创建真实服务。"""
        return {}

    @asynccontextmanager
    async def create_unified_lifespan(self, app: FastAPI) -> AsyncIterator[None]:
        """空的测试生命周期。"""
        yield


def create_fixture(root: Path) -> FastAPI:
    """所有初始化都发生在临时目录，Key 是明确的无生产权限测试值。"""
    from app.application import create_app
    from app.bootstrap import bootstrap_storage
    from app.core.config import settings
    from app.services.app_registry_service import upload_app
    from app.services.config_service import ConfigService
    from app.services.inspector_service import inspector_service
    from app.services.market_service import MarketService
    from app.storage.database import Database

    settings.mcpcat_config_path = str(root / "config.json")
    settings.mcpcat_artifacts_path = str(root / "skills")
    settings.mcpcat_apps_artifacts_path = str(root / "apps")
    settings.mcpcat_backups_path = str(root / "backups")
    settings.mcpcat_default_admin_key = "apps-browser-fixture-write"
    settings.mcpcat_default_read_key = "apps-browser-fixture-read"
    MarketService.refresh_async = lambda self: None
    inspector_service.start_cleanup_task = lambda: None
    database = Database(root / "fixture.db")
    bootstrap_storage(database)
    ConfigService.update_setting_section("skills_mcp", {"enabled": True})
    ConfigService.update_setting_section("apps", {"enabled": True})
    manager = EmptyManager()
    application = create_app(database=database, server_manager=manager)
    seed(database, upload_app)
    seed_native_fixture(database)
    seed_real_fixture(application, database, manager)
    seed_skills(database)
    # 使用应用工厂正式 /ui 路由，交付现有脚本构建到后端的 static，不能挂旧临时包冒充。
    return application


def seed_skills(database: Database) -> None:
    """本地旧版本与共享原文的双模式新版本，只包含客户端文件。"""
    from app.services.skill_profile_upload import upload_skill_profiles
    from app.services.skill_variant_publication import publish_content_version

    for version, selection in (("1.0.0", "local"), ("1.1.0", "both")):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr(
                "browser-skill/SKILL.md",
                "---\nname: browser-skill\ndescription: 当需要阅读示例资料和检查客户端文件时使用。\n---\n"
                f"# Skill 使用方式验收 {version}\n\n"
                "本地安装或由支持官方扩展的客户端远程读取；脚本只在客户环境执行。\n\n"
                "阅读 [使用说明](references/guide.md)，执行前自行审核脚本。\n",
            )
            archive.writestr(
                "browser-skill/references/guide.md",
                "# 使用说明\n\n- 原文件保持不变\n- 不在 mcpcat 执行脚本\n",
            )
            archive.writestr(
                "browser-skill/scripts/inspect.py",
                "print('client-side fixture only')\n",
            )
            archive.writestr("browser-skill/data/sample.bin", b"\x00\xff")
        upload_skill_profiles(
            database,
            content=output.getvalue(),
            version=version,
            selection=selection,
            metadata={"binding_kind": "independent", "required_tools": []},
        )
        publish_content_version(database, slug="browser-skill", version=version)


def seed_native_fixture(database: Database) -> None:
    """已发布的纯本地 HTML，验证真实管理预览 data 脚本 CSP。"""
    from app.services.app_publication_service import publish_app_version
    from app.services.app_registry_service import upload_app

    html = (
        "<!doctype html><html><body><h1>Native script preview</h1>"
        '<p id="result">Waiting</p><script>window.order=[];'
        'document.addEventListener("DOMContentLoaded",()=>window.dcl=window.order.join(",")==="one,two");'
        'window.addEventListener("load",()=>document.getElementById("result").textContent='
        '"order="+window.order.join(",")+"; dcl="+window.dcl+"; late="+window.late+"; async="+window.asyncDone);</script>'
        '<script defer src="one.js"></script><script defer src="two.js"></script>'
        '<script async src="async.js"></script><p id="late">Parsed after scripts</p>'
        "</body></html>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("index.html", html)
        archive.writestr(
            "one.js",
            "window.order.push('one');window.late=!!document.getElementById('late');",
        )
        archive.writestr("two.js", "window.order.push('two');")
        archive.writestr("async.js", "window.asyncDone=true;")
    upload_app(
        database,
        slug="native-timing",
        display_name="Native timing fixture",
        version="1.0.0",
        filename="app.zip",
        content=output.getvalue(),
    )
    publish_app_version(database, slug="native-timing", version="1.0.0", reviewed=True)


def seed_real_fixture(
    application: FastAPI, database: Database, manager: EmptyManager
) -> None:
    """只注册内存测试工具，调用/取消计数不接触用户资源。"""
    from fastmcp import FastMCP
    from fastmcp.tools import Tool

    from app.services.config_service import ConfigService
    from tests.browser.fixture_delivery_probe import DeliveryProbe

    counters = {"calls": 0, "slow_started": 0, "slow_cleaned": 0, "downloads": []}
    application.add_middleware(DeliveryProbe, counters=counters)
    source = FastMCP("preview-fixture")

    def store(value: int) -> dict:
        counters["calls"] += 1
        return {"saved": value}

    async def slow(value: int) -> dict:
        counters["slow_started"] += 1
        try:
            await asyncio.Event().wait()
        finally:
            counters["slow_cleaned"] += 1

    source.add_tool(Tool.from_function(store, name="store"))
    source.add_tool(Tool.from_function(slow, name="slow"))
    ConfigService.add_server_to_config(
        "preview-fixture",
        {"type": "stdio", "command": "fixture-never-executed", "require_auth": True},
    )
    manager.server_info = {"preview-fixture": {"status": "running", "mcp": source}}
    asyncio.run(seed_bound_app(database, manager))
    attach_evidence(application, counters)
    bundles = os.environ.get("MCPCAT_REFERENCE_HOST_BUNDLES")
    if bundles:
        from tests.browser.reference_apps_host import attach_reference_host
        from tests.browser.reference_upstream_apps import attach_upstream_catalog

        attach_upstream_catalog(application, manager, Path(bundles), counters)
        attach_reference_host(application, database, manager, Path(bundles))


def attach_evidence(application: FastAPI, counters: dict) -> None:
    """在正式静态挂载前提供本次夹具的只读交付和调用计数。"""
    from fastapi.responses import HTMLResponse

    @application.get("/ui/fixture-evidence", response_class=HTMLResponse)
    async def evidence() -> str:
        """公开值仅为此 loopback 测试夹具的计数，不包含资源或凭证。"""
        return "<h1>Fixture counters</h1><pre>" + json.dumps(counters) + "</pre>"

    # 正式 /ui 静态挂载已存在，夹具观测路由必须先匹配，避免被静态 404 遮蔽。
    route = next(
        item
        for item in application.router.routes
        if getattr(item, "path", None) == "/ui/fixture-evidence"
    )
    application.router.routes.remove(route)
    application.router.routes.insert(0, route)


async def seed_bound_app(database: Database, manager: EmptyManager) -> None:
    """通过真实审核服务冻结两个明确绑定；不预先执行工具。"""
    from app.models.app_bindings import AppToolBinding
    from app.services.app_publication_service import publish_app_version
    from app.services.app_registry_service import upload_app
    from app.services.app_tool_bindings import (
        AppToolResolver,
        publication_validator,
        replace_app_bindings,
    )

    upload_app(
        database,
        slug="real-demo",
        display_name="真实调用测试 App",
        version="1.0.0",
        filename="app.json",
        content=json.dumps(
            {
                "state": {"saved": "尚未调用"},
                "view": {"type": "Column", "children": bound_buttons()},
            }
        ).encode(),
    )
    resolver = AppToolResolver(database, manager, lambda: True)
    await replace_app_bindings(
        database,
        resolver,
        slug="real-demo",
        version="1.0.0",
        bindings=[
            AppToolBinding(alias=name, server_name="preview-fixture", tool_name=name)
            for name in ("store", "slow")
        ],
    )
    checked = await publication_validator(database, resolver, "real-demo", "1.0.0")
    publish_app_version(
        database,
        slug="real-demo",
        version="1.0.0",
        reviewed=True,
        validate_bindings=checked,
    )


def bound_buttons() -> list[dict]:
    """同一份 Prefab 动作定义用于模拟与真实确认测试。"""
    buttons = [
        {"type": "Heading", "content": "真实调用联调"},
        {"type": "Text", "content": "写入结果：{{ saved }}"},
    ]
    for alias, label in (("store", "测试写入"), ("slow", "测试慢调用")):
        buttons.append(
            {
                "type": "Button",
                "label": label,
                "onClick": {
                    "action": "toolCall",
                    "tool": alias,
                    "arguments": {"value": 7},
                    "onSuccess": {
                        "action": "setState",
                        "key": "saved",
                        "value": "{{ $result.saved }}",
                    },
                },
            }
        )
    return buttons


def seed(database: Database, uploader: Callable[..., Any]) -> None:
    """普通 Prefab 状态交互，不含真实工具或认证信息。"""
    wire = {
        "state": {"count": 1},
        "view": {
            "type": "Column",
            "children": [
                {"type": "Heading", "content": "隔离预览联调"},
                {"type": "Text", "content": "计数：{{ count }}"},
                {
                    "type": "Button",
                    "label": "增加计数",
                    "onClick": {
                        "action": "setState",
                        "key": "count",
                        "value": "{{ count + 1 }}",
                    },
                },
            ],
        },
    }
    uploader(
        database,
        slug="browser-demo",
        display_name="浏览器联调 App",
        version="1.0.0",
        filename="app.json",
        content=json.dumps(wire).encode(),
    )


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    with TemporaryDirectory(prefix="mcpcat-apps-ui-") as directory:
        os.chdir(directory)
        import uvicorn

        uvicorn.run(create_fixture(Path(directory)), host="127.0.0.1", port=18765)
