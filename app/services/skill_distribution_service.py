"""Registry 索引、版本下载和可复现全量 bundle。"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Optional, Union

from filelock import FileLock

from app.core.config import settings
from app.services.skill_package_validator import build_deterministic_skill_zip
from app.storage.database import Database, resolve_storage_path
from app.storage.skill_repositories import SkillDomainError
from app.storage.unit_of_work import UnitOfWork

REGISTRY_SCHEMA_VERSION = "1.0.0"
REGISTRY_API_VERSION = "v1"
MIN_CLI_VERSION = "0.1.0"
RECOMMENDED_CLI_VERSION = "1.0.1"
SKILL_FILE_PREVIEW_MAX_BYTES = 512 * 1024


@dataclass(frozen=True)
class DownloadArtifact:
    path: Path
    slug: str
    version: str
    sha256: str
    size: int


@dataclass(frozen=True)
class SkillFilePreview:
    """一个经过路径、权限和制品完整性校验的文本预览。"""

    path: str
    media_type: str
    size: int
    is_markdown: bool
    previewable: bool
    content: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class BundleArtifact:
    id: int
    path: Path
    sha256: str
    size: int
    manifest: dict[str, Any]


def _artifact_root(root: Optional[Union[str, Path]] = None) -> Path:
    return resolve_storage_path(root or settings.mcpcat_artifacts_path)


def _verify_file(path: Path, *, size: int, sha256: str) -> bool:
    if not path.is_file() or path.stat().st_size != size:
        return False
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest == sha256


def resolve_version_download(
    database: Database,
    *,
    slug: str,
    version: str,
    include_drafts: bool = False,
    artifact_root: Optional[Union[str, Path]] = None,
) -> DownloadArtifact:
    """解析明确版本，并在每次下载前验证制品完整性。"""

    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug(slug)
        if skill is None:
            raise SkillDomainError("Skill 不存在")
        version_row = unit.skill_versions.get(skill.id, version)
        if version_row is None or (
            not include_drafts and version_row.status not in {"published", "deprecated"}
        ):
            raise SkillDomainError("Skill 版本不可见")
        artifact = unit.skill_artifacts.get_for_version(version_row.id)
        if artifact is None:
            raise SkillDomainError("Skill 版本没有制品")
        relative_path = artifact.relative_path
        expected_size = artifact.size
        expected_sha = artifact.sha256

    path = _artifact_root(artifact_root) / relative_path
    if not _verify_file(path, size=expected_size, sha256=expected_sha):
        with UnitOfWork(database) as unit:
            skill = unit.skills.get_by_slug(slug)
            version_row = unit.skill_versions.get(skill.id, version)
            artifact = unit.skill_artifacts.get_for_version(version_row.id)
            unit.skill_artifacts.set_integrity(
                artifact, "corrupt" if path.exists() else "missing"
            )
            unit.commit()
        raise SkillDomainError("Skill 制品完整性校验失败")
    return DownloadArtifact(path, slug, version, expected_sha, expected_size)


def resolve_version_file_preview(
    database: Database,
    *,
    slug: str,
    version: str,
    file_path: str,
    include_drafts: bool = False,
    artifact_root: Optional[Union[str, Path]] = None,
) -> SkillFilePreview:
    """按需读取 Skill ZIP 内的一个安全文本文件。"""

    candidate = PurePosixPath(file_path)
    if (
        not file_path
        or candidate.is_absolute()
        or candidate.as_posix() != file_path
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or not candidate.parts
        or candidate.parts[0] != slug
    ):
        raise SkillDomainError("无效的 Skill 文件路径")

    artifact = resolve_version_download(
        database,
        slug=slug,
        version=version,
        include_drafts=include_drafts,
        artifact_root=artifact_root,
    )
    try:
        with zipfile.ZipFile(artifact.path) as archive:
            try:
                info = archive.getinfo(file_path)
            except KeyError as error:
                raise SkillDomainError("Skill 文件不存在") from error
            if info.is_dir():
                raise SkillDomainError("Skill 文件路径指向目录")
            if info.flag_bits & 0x1:
                raise SkillDomainError("不支持预览加密的 Skill 文件")
            if info.file_size > SKILL_FILE_PREVIEW_MAX_BYTES:
                return SkillFilePreview(
                    path=file_path,
                    media_type=mimetypes.guess_type(file_path)[0]
                    or "application/octet-stream",
                    size=info.file_size,
                    is_markdown=False,
                    previewable=False,
                    reason="too_large",
                )
            content = archive.read(info)
    except zipfile.BadZipFile as error:
        raise SkillDomainError("Skill 制品不是有效的 ZIP") from error

    suffix = candidate.suffix.lower()
    is_markdown = suffix in {".md", ".markdown"}
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return SkillFilePreview(
            path=file_path,
            media_type=mimetypes.guess_type(file_path)[0] or "application/octet-stream",
            size=len(content),
            is_markdown=is_markdown,
            previewable=False,
            reason="binary",
        )
    return SkillFilePreview(
        path=file_path,
        media_type=mimetypes.guess_type(file_path)[0] or "text/plain",
        size=len(content),
        is_markdown=is_markdown,
        previewable=True,
        content=text,
    )


def build_registry_index(database: Database, *, base_url: str) -> dict[str, Any]:
    """构建只含已发布可见版本的稳定机器索引。"""

    items = []
    with UnitOfWork(database) as unit:
        for skill in unit.skills.list_visible():
            version = skill.latest_published_version
            if not version:
                continue
            version_row = unit.skill_versions.get(skill.id, version)
            artifact = unit.skill_artifacts.get_for_version(version_row.id)
            if artifact is None or artifact.integrity_status != "ok":
                continue
            items.append(
                {
                    "slug": skill.slug,
                    "display_name": skill.display_name,
                    "description": skill.description,
                    "source_type": skill.source_type,
                    "status": skill.status,
                    "latest_published_version": version,
                    "compatibility": version_row.compatibility_json,
                    "sha256": artifact.sha256,
                    "size": artifact.size,
                    "download_url": (
                        f"{base_url.rstrip('/')}/api/skills/{skill.slug}/versions/"
                        f"{version}/download"
                    ),
                }
            )
    return {
        "registry_schema_version": REGISTRY_SCHEMA_VERSION,
        "api_version": REGISTRY_API_VERSION,
        "skills": items,
    }


def stable_etag(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return f'"{hashlib.sha256(payload).hexdigest()}"'


def _snapshot_items(database: Database) -> list[dict[str, Any]]:
    items = []
    with UnitOfWork(database) as unit:
        for skill in unit.skills.list_visible():
            if not skill.latest_published_version:
                continue
            version = unit.skill_versions.get(skill.id, skill.latest_published_version)
            artifact = unit.skill_artifacts.get_for_version(version.id)
            if artifact is None or artifact.integrity_status != "ok":
                continue
            items.append(
                {
                    "slug": skill.slug,
                    "version": version.version,
                    "sha256": artifact.sha256,
                    "size": artifact.size,
                    "relative_path": artifact.relative_path,
                }
            )
    return items


def build_latest_bundle(
    database: Database,
    *,
    base_url: str,
    artifact_root: Optional[Union[str, Path]] = None,
) -> BundleArtifact:
    """缓存并返回调用者可见最新版本集合的确定性快照。"""

    root = _artifact_root(artifact_root)
    items = _snapshot_items(database)
    snapshot_payload = json.dumps(
        items, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    snapshot_key = hashlib.sha256(snapshot_payload).hexdigest()
    with UnitOfWork(database) as unit:
        cached = unit.skill_bundles.get_by_snapshot_key(snapshot_key)
        if cached is not None:
            path = root / cached.relative_path
            if _verify_file(
                path,
                size=path.stat().st_size if path.exists() else -1,
                sha256=cached.sha256,
            ):
                return BundleArtifact(
                    cached.id,
                    path,
                    cached.sha256,
                    path.stat().st_size,
                    cached.manifest_json,
                )

    files: dict[str, bytes] = {}
    manifest_items = []
    for item in items:
        artifact = resolve_version_download(
            database,
            slug=item["slug"],
            version=item["version"],
            artifact_root=root,
        )
        with zipfile.ZipFile(artifact.path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if info.filename in files:
                    raise SkillDomainError("bundle 中存在重复 Skill 路径")
                files[info.filename] = archive.read(info)
        manifest_items.append(
            {
                "slug": item["slug"],
                "version": item["version"],
                "sha256": item["sha256"],
            }
        )
    manifest = {
        "schema_version": "1.0.0",
        "bundle_id": snapshot_key,
        "registry": base_url.rstrip("/"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "skills": manifest_items,
    }
    files[".mcpcat-bundle.json"] = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    content = build_deterministic_skill_zip(files)
    digest = hashlib.sha256(content).hexdigest()
    relative = f"bundles/{snapshot_key}.zip"
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(target.with_suffix(".zip.lock")))
    with lock:
        with UnitOfWork(database) as unit:
            cached = unit.skill_bundles.get_by_snapshot_key(snapshot_key)
            if cached is not None and target.exists():
                return BundleArtifact(
                    cached.id,
                    target,
                    cached.sha256,
                    target.stat().st_size,
                    cached.manifest_json,
                )
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            target.chmod(0o600)
            try:
                with UnitOfWork(database) as unit:
                    row = unit.skill_bundles.create(
                        snapshot_key=snapshot_key,
                        manifest=manifest,
                        sha256=digest,
                        relative_path=relative,
                    )
                    unit.commit()
                    bundle_id = row.id
            except Exception:
                target.unlink(missing_ok=True)
                raise
        finally:
            temporary.unlink(missing_ok=True)
    return BundleArtifact(bundle_id, target, digest, len(content), manifest)


def resolve_bundle_download(
    database: Database,
    *,
    bundle_id: int,
    artifact_root: Optional[Union[str, Path]] = None,
) -> BundleArtifact:
    """按历史 bundle ID 复现明确集合。"""

    with UnitOfWork(database) as unit:
        row = unit.skill_bundles.get_by_id(bundle_id)
        if row is None:
            raise SkillDomainError("bundle 不存在")
        relative = row.relative_path
        sha256 = row.sha256
        manifest = row.manifest_json
    path = _artifact_root(artifact_root) / relative
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
        raise SkillDomainError("bundle 完整性校验失败")
    return BundleArtifact(bundle_id, path, sha256, path.stat().st_size, manifest)
