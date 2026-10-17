"""变体制品原始字节读取与清单校验，不解压或执行文件。"""

import hashlib
import io
import mimetypes
import zipfile
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.skill_package_validator import validate_skill_zip
from app.services.verified_artifacts import ArtifactReadError, read_verified_artifact
from app.storage.database import resolve_storage_path
from app.storage.models import SkillArtifactRecord
from app.storage.skill_repositories import SkillDomainError
from app.storage.skill_variant_models import SkillContentVariantRecord
from app.storage.unit_of_work import UnitOfWork


def inspect_variant_zip(content: bytes, *, remote: bool = False) -> tuple[dict, dict]:
    """从原 ZIP 得到完整 frontmatter、清单，摘要按实际返回字节计算。"""
    validation = validate_skill_zip(content)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        files = [entry for entry in archive.infolist() if not entry.is_dir()]
        if remote and (
            len(files) > 512
            or sum(entry.file_size for entry in files) > 16 * 1024 * 1024
        ):
            raise SkillDomainError("远程 Skill 最多 512 文件、16 MiB 原始内容")
        manifest = {
            "files": [
                {
                    "path": entry.filename,
                    "size": entry.file_size,
                    "sha256": hashlib.sha256(archive.read(entry)).hexdigest(),
                }
                for entry in sorted(files, key=lambda item: item.filename)
            ]
        }
    return validation.metadata, manifest


def variant_artifact(unit: UnitOfWork, variant: SkillContentVariantRecord) -> Any:
    """解析明确引用，不因 profile 缺失而跨模式回退。"""
    if variant.artifact_id is not None:
        artifact = unit.session.get(SkillArtifactRecord, variant.artifact_id)
    else:
        artifact = unit.skill_variants.get_remote_artifact(variant.remote_artifact_id)
    if artifact is None or artifact.integrity_status != "ok":
        raise SkillDomainError("变体没有完整可用制品")
    return artifact


def read_checked_zip(artifact: Any, *, artifact_root: Path | None = None) -> bytes:
    """SHA/size 验证覆盖整个制品，路径不得逃逸存储根目录。"""
    try:
        return read_verified_artifact(
            resolve_storage_path(artifact_root or settings.mcpcat_artifacts_path),
            relative_path=artifact.relative_path,
            size=artifact.size,
            sha256=artifact.sha256,
        )
    except ArtifactReadError as error:
        message = (
            "变体制品缺失或路径无效"
            if str(error) == "path_invalid"
            else "变体制品完整性校验失败"
        )
        raise SkillDomainError(message) from error


def read_manifest_file(content: bytes, manifest: dict, path: str) -> bytes:
    """只返回清单中单个文件，禁止未知路径和任意磁盘读取。"""
    entry = next(
        (entry for entry in manifest.get("files", []) if entry.get("path") == path),
        None,
    )
    if entry is None:
        raise SkillDomainError("Skill 文件不在版本清单中")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            data = archive.read(path)
    except (KeyError, zipfile.BadZipFile) as error:
        raise SkillDomainError("Skill 文件不存在或制品损坏") from error
    if (
        len(data) != entry["size"]
        or hashlib.sha256(data).hexdigest() != entry["sha256"]
    ):
        raise SkillDomainError("Skill 文件摘要不一致")
    return data


def file_media_type(path: str, content: bytes) -> str:
    """保留自定义文件类型；无扩展名 UTF-8 文本使用 text/plain。"""
    guessed = mimetypes.guess_type(path)[0]
    if guessed:
        return guessed
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return "application/octet-stream"
    return "text/plain"
