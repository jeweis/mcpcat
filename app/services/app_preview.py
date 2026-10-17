"""明确版本的纯内容预览，不运行前端、用户后端或任何真实工具。"""

from starlette.concurrency import run_in_threadpool

from app.services.app_content_service import AppDomainError, load_app_content
from app.services.app_rendering import RenderedApp, app_callback_name, render_app
from app.services.app_tool_bindings import AppToolResolver, publication_validator
from app.storage.database import Database
from app.storage.unit_of_work import UnitOfWork


async def preview_app(
    database: Database,
    resolver: AppToolResolver,
    *,
    slug: str,
    version: str,
    include_drafts: bool,
) -> dict:
    """制品和绑定前后复核，草稿也不能取得失效来源的预览授权。"""
    if not resolver.authorize():
        raise AppDomainError("当前身份无权预览 App", 403)
    info, content = await run_in_threadpool(
        load_app_content,
        database,
        slug=slug,
        version=version,
        include_drafts=include_drafts,
    )
    with UnitOfWork(database) as unit:
        app_id = unit.apps.get(slug).id
    await publication_validator(database, resolver, slug, version)
    rendered = await run_in_threadpool(render_app, info, content, app_id)
    current, _ = await run_in_threadpool(
        load_app_content,
        database,
        slug=slug,
        version=version,
        include_drafts=include_drafts,
    )
    if current != info:
        raise AppDomainError("App 配置在预览装配期间已改变，请刷新后重试")
    await publication_validator(database, resolver, slug, version)
    if not resolver.authorize():
        raise AppDomainError("当前身份无权预览 App", 403)
    return _preview_data(slug, version, app_id, info, rendered)


def _preview_data(
    slug: str, version: str, app_id: int, info: dict, rendered: RenderedApp
) -> dict:
    """只返回公开回调名及静态界面，不传认证上下文。"""
    return {
        "slug": slug,
        "version": version,
        "mode": "mock",
        "html": rendered.html,
        "initial_result": rendered.structured_content,
        "allowed_tools": [
            app_callback_name(app_id, version, item["alias"])
            for item in info["bindings"]
        ],
        "real_callable": info["status"] == "published",
        "tools": [
            {
                "name": app_callback_name(app_id, version, item["alias"]),
                **{key: item[key] for key in ("alias", "server_name", "tool_name")},
            }
            for item in info["bindings"]
        ],
        "sha256": rendered.sha256,
        "renderer_version": rendered.renderer_version,
        "requested_metadata": rendered.metadata,
    }
