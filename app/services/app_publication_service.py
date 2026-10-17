"""Apps 草稿审核与发布/撤销，已审核版本不可再修改。"""

from collections.abc import Callable

from filelock import FileLock
from sqlalchemy import select

from app.models.app_review import AppCSP, AppPermissions
from app.services.app_content_service import (
    AppDomainError,
    app_files,
    load_app_content,
    visible_version,
)
from app.services.app_rendering import render_app
from app.storage.app_models import AppToolBindingRecord, AppVersionRecord
from app.storage.database import Database
from app.storage.models import utc_now
from app.storage.unit_of_work import UnitOfWork


def app_version_lock(database: Database, version_id: int) -> FileLock:
    """保护固定版本的配置/发布状态，不将 slug 或凭证拼进路径。"""
    return FileLock(str(database.path) + f".app-version-{version_id}.lock", timeout=5)


def configure_app_draft(
    database: Database,
    *,
    slug: str,
    version: str,
    csp: AppCSP,
    permissions: AppPermissions,
    changelog: str,
) -> None:
    """只修改草稿审核元数据；内容变化必须上传不同 SemVer。"""
    with UnitOfWork(database) as unit:
        version_id = visible_version(unit, slug, version, True).id
    with app_version_lock(database, version_id), UnitOfWork(database) as unit:
        row = visible_version(unit, slug, version, True)
        if row.status != "draft":
            raise AppDomainError("已发布或撤销的 App 版本不可修改")
        row.csp_json = csp.model_dump(by_alias=True, exclude_none=True)
        row.permissions_json = permissions.model_dump(by_alias=True, exclude_none=True)
        row.changelog = changelog
        unit.commit()


def publish_app_version(
    database: Database,
    *,
    slug: str,
    version: str,
    reviewed: bool,
    public: bool = False,
    validate_bindings: Callable[[list[AppToolBindingRecord]], None] | None = None,
) -> None:
    """发布前重验原字节和审核配置；未知绑定不得绕过运行检查。"""
    if reviewed is not True:
        raise AppDomainError("请确认已审核 App 内容、权限和工具绑定", 400)
    with UnitOfWork(database) as unit:
        version_id = visible_version(unit, slug, version, True).id
    with app_version_lock(database, version_id), UnitOfWork(database) as unit:
        row = visible_version(unit, slug, version, True)
        if row.status != "draft":
            raise AppDomainError("只能发布 App 草稿，请创建新版本")
        info, content = load_app_content(
            database, slug=slug, version=version, include_drafts=True
        )
        app_files(info, content)
        AppCSP.model_validate(row.csp_json)
        AppPermissions.model_validate(row.permissions_json)
        bindings = list(
            unit.session.scalars(
                select(AppToolBindingRecord).where(
                    AppToolBindingRecord.app_version_id == row.id
                )
            )
        )
        if bindings and validate_bindings is None:
            raise AppDomainError("工具绑定尚未经过当前运行来源验证")
        if validate_bindings is not None:
            validate_bindings(bindings)
        rendered = render_app(info, content, row.app_id)
        if validate_bindings is not None:
            validate_bindings(bindings)
        row.rendered_sha256, row.rendering_version = (
            rendered.sha256,
            rendered.renderer_version,
        )
        row.rendered_payload_sha256 = rendered.payload_sha256()
        row.status = "published"
        row.published_at = utc_now()
        app = unit.apps.get(slug)
        app.status = "published"
        app.visibility = "public" if public else "private"
        unit.commit()


def withdraw_app_version(database: Database, *, slug: str, version: str) -> None:
    """撤销停止分发但保留制品；重新发布需要新版本与新审核。"""
    with UnitOfWork(database) as unit:
        version_id = visible_version(unit, slug, version, True).id
    with app_version_lock(database, version_id), UnitOfWork(database) as unit:
        row = visible_version(unit, slug, version, True)
        if row.status not in {"published", "deprecated"}:
            raise AppDomainError("只能撤销已发布 App 版本")
        row.status = "deprecated"
        other = unit.session.scalar(
            select(AppVersionRecord.id).where(
                AppVersionRecord.app_id == row.app_id,
                AppVersionRecord.status == "published",
                AppVersionRecord.id != row.id,
            )
        )
        if other is None:
            unit.apps.get(slug).status = "deprecated"
        unit.commit()
