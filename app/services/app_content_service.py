"""Apps 固定版本和文件读取，只返回已校验原始字节。"""

from typing import Any

from sqlalchemy import select

from app.services.app_package_validator import safe_app_path, validate_app_package
from app.services.app_registry_service import read_app_artifact
from app.storage.app_models import (
    AppArtifactRecord,
    AppToolBindingRecord,
    AppVersionRecord,
)
from app.storage.database import Database
from app.storage.skill_repositories import semver_key
from app.storage.unit_of_work import UnitOfWork


class AppDomainError(ValueError):
    """可安全展示的业务错误，区分缺失与状态冲突。"""

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


def version_data(unit: UnitOfWork, row: AppVersionRecord) -> dict[str, Any]:
    """序列化管理元数据，不泄露磁盘路径或认证配置。"""
    artifact = unit.session.scalar(
        select(AppArtifactRecord).where(AppArtifactRecord.app_version_id == row.id)
    )
    bindings = unit.session.scalars(
        select(AppToolBindingRecord).where(
            AppToolBindingRecord.app_version_id == row.id
        )
    ).all()
    return {
        "version": row.version,
        "status": row.status,
        "content_format": row.content_format,
        "entrypoint": row.entrypoint,
        "manifest": row.manifest_json,
        "csp": row.csp_json,
        "permissions": row.permissions_json,
        "renderer_version": row.renderer_version,
        "rendered_sha256": row.rendered_sha256,
        "rendering_version": row.rendering_version,
        "rendered_payload_sha256": row.rendered_payload_sha256,
        "changelog": row.changelog,
        "created_at": row.created_at.isoformat(),
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "sha256": artifact.sha256 if artifact else None,
        "size": artifact.size if artifact else None,
        "media_type": artifact.media_type if artifact else None,
        "bindings": [
            {
                "alias": item.alias,
                "server_name": item.server_name,
                "tool_name": item.tool_name,
            }
            for item in bindings
        ],
    }


def visible_version(
    unit: UnitOfWork, slug: str, version: str, include_drafts: bool
) -> AppVersionRecord:
    """READ 管理用户只见已发布内容；撤销后也不能继续下载。"""
    app = unit.apps.get(slug)
    row = unit.apps.get_version(app.id, version) if app else None
    if row is None or (
        not include_drafts and (app.status != "published" or row.status != "published")
    ):
        raise AppDomainError("App 版本不存在", 404)
    return row


def list_apps(database: Database, *, include_drafts: bool = False) -> list[dict]:
    """按稳定 slug 列表，latest 从当前可见 SemVer 计算。"""
    from app.storage.app_models import AppRecord

    with UnitOfWork(database) as unit:
        result = []
        for app in unit.session.scalars(select(AppRecord).order_by(AppRecord.slug)):
            versions = list(
                unit.session.scalars(
                    select(AppVersionRecord).where(AppVersionRecord.app_id == app.id)
                )
            )
            visible = (
                versions
                if include_drafts
                else [
                    row
                    for row in versions
                    if row.status == "published" and app.status == "published"
                ]
            )
            if not visible:
                continue
            latest = max(visible, key=lambda row: semver_key(row.version))
            result.append(
                {
                    "slug": app.slug,
                    "display_name": app.display_name,
                    "description": app.description,
                    "source_type": app.source_type,
                    "status": app.status,
                    "visibility": app.visibility,
                    "latest_version": latest.version,
                }
            )
        return result


def app_detail(database: Database, *, slug: str, include_drafts: bool = False) -> dict:
    """管理详情只包含当前身份可见的明确版本。"""
    with UnitOfWork(database) as unit:
        app = unit.apps.get(slug)
        if not app or (not include_drafts and app.status != "published"):
            raise AppDomainError("App 不存在", 404)
        rows = unit.session.scalars(
            select(AppVersionRecord).where(AppVersionRecord.app_id == app.id)
        ).all()
        versions = [
            version_data(unit, row)
            for row in sorted(
                rows, key=lambda row: semver_key(row.version), reverse=True
            )
            if include_drafts or row.status == "published"
        ]
        if not versions:
            raise AppDomainError("App 不存在", 404)
        return {
            "slug": slug,
            "display_name": app.display_name,
            "description": app.description,
            "source_type": app.source_type,
            "status": app.status,
            "visibility": app.visibility,
            "versions": versions,
        }


def load_app_content(
    database: Database, *, slug: str, version: str, include_drafts: bool = False
) -> tuple[dict, bytes]:
    """明确版本读取；SHA 与完整清单都需一致，不隐式取 latest。"""
    with UnitOfWork(database) as unit:
        row = visible_version(unit, slug, version, include_drafts)
        info = version_data(unit, row)
        content = read_app_artifact(database, version_id=row.id)
    app_files(info, content)
    return info, content


def app_files(info: dict, content: bytes) -> dict[str, bytes]:
    """复验原始包及清单，DB 清单改变不能借此扩张路径。"""
    filename = {
        "application/zip": "app.zip",
        "application/json": "app.json",
        "text/html": "index.html",
    }.get(info["media_type"])
    if filename is None:
        raise AppDomainError("App 制品格式无效")
    result = validate_app_package(
        content, filename=filename, entrypoint=info["entrypoint"]
    )
    if (
        result.manifest != info["manifest"]
        or result.content_format != info["content_format"]
    ):
        raise AppDomainError("App 文件清单完整性校验失败")
    return result.files


def load_app_file(
    database: Database,
    *,
    slug: str,
    version: str,
    path: str,
    include_drafts: bool = False,
) -> bytes:
    """仅按原始包中经验证的路径读取，不读取宿主同名文件。"""
    safe_app_path(path)
    info, content = load_app_content(
        database, slug=slug, version=version, include_drafts=include_drafts
    )
    data = app_files(info, content).get(path)
    if data is None:
        raise AppDomainError("文件不在 App 版本中", 404)
    return data
