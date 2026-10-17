"""Apps 上传与不可变原始制品存储，不执行内容。"""

from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.services.app_package_validator import validate_app_package
from app.services.verified_artifacts import ArtifactReadError, read_verified_artifact
from app.storage.app_models import AppArtifactRecord
from app.storage.database import Database, resolve_storage_path
from app.storage.unit_of_work import UnitOfWork


def upload_app(
    database: Database,
    *,
    slug: str,
    display_name: str,
    version: str,
    content: bytes,
    filename: str,
    entrypoint: str | None = None,
    actor: str | None = None,
    artifact_root: Path | None = None,
) -> int:
    """校验后创建草稿及原始制品，失败回滚且不覆盖历史版本。"""
    if not display_name.strip() or len(display_name) > 255:
        raise ValueError("App 显示名称不能为空或超过 255 字符")
    validation = validate_app_package(content, filename=filename, entrypoint=entrypoint)
    root = resolve_storage_path(artifact_root or settings.mcpcat_apps_artifacts_path)
    created_path = None
    try:
        with UnitOfWork(database) as unit:
            app = unit.apps.get(slug)
            if app is None:
                app = unit.apps.create(
                    slug=slug, display_name=display_name, actor=actor
                )
            if app.source_type != "uploaded":
                raise ValueError("同名 App 已由上游占用")
            row = unit.apps.create_version(
                app_id=app.id,
                version=version,
                content_format=validation.content_format,
                entrypoint=validation.entrypoint,
                manifest=validation.manifest,
            )
            row.created_by = actor
            if validation.content_format == "prefab":
                row.renderer_version = "0.20.2"
            relative = f"{app.id}/{uuid4().hex}.package"
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as output:
                created_path = destination
                output.write(validation.original_content)
                output.flush()
            unit.session.add(
                AppArtifactRecord(
                    app_version_id=row.id,
                    relative_path=relative,
                    size=len(content),
                    media_type=(
                        "application/zip"
                        if filename.lower().endswith(".zip")
                        else (
                            "application/json"
                            if validation.content_format == "prefab"
                            else "text/html"
                        )
                    ),
                    sha256=validation.sha256,
                )
            )
            unit.commit()
            return row.id
    except Exception:
        if created_path is not None:
            created_path.unlink(missing_ok=True)
        raise


def read_app_artifact(
    database: Database, *, version_id: int, artifact_root: Path | None = None
) -> bytes:
    """管理层读取原始内容并验证 SHA；MCP 分发另加发布与身份校验。"""
    from sqlalchemy import select

    with UnitOfWork(database) as unit:
        artifact = unit.session.scalar(
            select(AppArtifactRecord).where(
                AppArtifactRecord.app_version_id == version_id,
            )
        )
        if artifact is None:
            raise ValueError("App 制品不存在")
        if artifact.integrity_status != "ok":
            raise ValueError("App 制品完整性校验失败")
        try:
            return read_verified_artifact(
                resolve_storage_path(
                    artifact_root or settings.mcpcat_apps_artifacts_path
                ),
                relative_path=artifact.relative_path,
                size=artifact.size,
                sha256=artifact.sha256,
                max_bytes=settings.mcpcat_app_max_bytes,
            )
        except ArtifactReadError as error:
            message = (
                "App 制品路径或文件无效"
                if str(error) == "path_invalid"
                else "App 制品完整性校验失败"
            )
            raise ValueError(message) from error
