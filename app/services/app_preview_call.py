"""管理预览的显式真实调用，复用已发布 Apps 的普通授权/审计链。"""

import json
from typing import Any

from app.services.app_content_service import AppDomainError
from app.services.app_preview import preview_app
from app.services.app_rendering import app_callback_name
from app.services.app_tool_bindings import (
    AppToolResolver,
    app_binding_rows,
    call_app_tool,
)
from app.storage.database import Database
from app.storage.unit_of_work import UnitOfWork


def _checked_arguments(arguments: dict) -> None:
    """确认针对一次有界 JSON 参数，不执行代码或接受非有限数字。"""
    try:
        content = json.dumps(arguments, allow_nan=False, ensure_ascii=False).encode()
    except (TypeError, ValueError, RecursionError) as error:
        raise AppDomainError("App 工具参数必须为合法 JSON", 400) from error
    if len(content) > 512 * 1024:
        raise AppDomainError("App 工具参数超过预览限制", 413)


def _alias(database: Database, slug: str, version: str, name: str) -> str:
    """只反查当前版本的公开回调名，不接受直接来源名或任意工具名。"""
    rows = app_binding_rows(database, slug, version)
    with UnitOfWork(database) as unit:
        app_id = unit.apps.get(slug).id
    match = next(
        (
            row["alias"]
            for row in rows
            if app_callback_name(app_id, version, row["alias"]) == name
        ),
        None,
    )
    if match is None:
        raise AppDomainError("工具不在当前 App 版本的预览范围内", 403)
    return match


async def call_preview_tool(
    database: Database,
    resolver: AppToolResolver,
    *,
    slug: str,
    version: str,
    name: str,
    arguments: dict,
    confirmed: bool,
) -> dict[str, Any]:
    """仅审核发布版本可真实测试；确认不代替当前身份和绑定鉴权。"""
    if confirmed is not True:
        raise AppDomainError("真实工具测试需要明确确认，不会自动执行", 400)
    _checked_arguments(arguments)
    prepared = await preview_app(
        database, resolver, slug=slug, version=version, include_drafts=False
    )
    if name not in prepared["allowed_tools"]:
        raise AppDomainError("工具不在当前 App 版本的预览范围内", 403)
    alias = _alias(database, slug, version, name)
    result = await call_app_tool(
        database, resolver, slug=slug, version=version, alias=alias, arguments=arguments
    )
    return result.model_dump(mode="json", by_alias=True, exclude_none=True)
