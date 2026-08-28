"""Agent Skill ZIP 的纯静态安全校验和可复现规范化。"""

from __future__ import annotations

import hashlib
import io
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import yaml

from app.core.config import settings
from app.storage.skill_repositories import validate_skill_slug

SCRIPT_SUFFIXES = {
    ".bat",
    ".cmd",
    ".js",
    ".mjs",
    ".ps1",
    ".py",
    ".rb",
    ".sh",
    ".ts",
}
WINDOWS_ABSOLUTE_PATTERN = re.compile(r"^[A-Za-z]:")


class SkillPackageValidationError(ValueError):
    """上传包不满足安全或 Agent Skills 规范。"""


@dataclass(frozen=True)
class SkillFileEntry:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class SkillPackageValidation:
    name: str
    description: str
    compatibility: str | None
    metadata: dict[str, Any]
    files: list[SkillFileEntry]
    scripts: list[str]
    normalized_zip: bytes
    sha256: str


def _safe_path(filename: str) -> PurePosixPath:
    if not filename or "\x00" in filename or "\\" in filename:
        raise SkillPackageValidationError("ZIP 包含无效或不明确的路径")
    if filename.startswith("/") or WINDOWS_ABSOLUTE_PATTERN.match(filename):
        raise SkillPackageValidationError("ZIP 不得包含绝对路径")
    path = PurePosixPath(filename)
    if ".." in path.parts or "." in path.parts:
        raise SkillPackageValidationError("ZIP 不得包含父目录穿越路径")
    return path


def _validate_entry_type(info: zipfile.ZipInfo) -> None:
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if mode == 0 or file_type == 0 or info.is_dir() or stat.S_ISREG(mode):
        return
    if stat.S_ISLNK(mode):
        raise SkillPackageValidationError("ZIP 不得包含符号链接")
    raise SkillPackageValidationError("ZIP 不得包含设备文件或其他特殊文件")


def _parse_frontmatter(content: bytes, root_name: str) -> dict[str, Any]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SkillPackageValidationError("SKILL.md 必须是 UTF-8") from error
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillPackageValidationError("SKILL.md 必须以 YAML frontmatter 开始")
    try:
        closing = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as error:
        raise SkillPackageValidationError(
            "SKILL.md frontmatter 缺少结束分隔符"
        ) from error
    try:
        metadata = yaml.safe_load("\n".join(lines[1:closing]))
    except yaml.YAMLError as error:
        raise SkillPackageValidationError(
            "SKILL.md frontmatter 不是合法 YAML"
        ) from error
    if not isinstance(metadata, dict):
        raise SkillPackageValidationError("SKILL.md frontmatter 必须是对象")
    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str):
        raise SkillPackageValidationError("SKILL.md 缺少字符串 name")
    validate_skill_slug(name)
    if name != root_name:
        raise SkillPackageValidationError("SKILL.md name 必须与 Skill 根目录一致")
    if not isinstance(description, str) or not description.strip():
        raise SkillPackageValidationError("SKILL.md description 必须是非空字符串")
    if len(description) > 1024:
        raise SkillPackageValidationError("SKILL.md description 不得超过 1024 字符")
    compatibility = metadata.get("compatibility")
    if compatibility is not None and (
        not isinstance(compatibility, str)
        or not compatibility
        or len(compatibility) > 500
    ):
        raise SkillPackageValidationError("compatibility 必须是 1-500 字符字符串")
    custom_metadata = metadata.get("metadata")
    if custom_metadata is not None and (
        not isinstance(custom_metadata, dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in custom_metadata.items()
        )
    ):
        raise SkillPackageValidationError("metadata 必须是字符串到字符串的映射")
    allowed_tools = metadata.get("allowed-tools")
    if allowed_tools is not None and not isinstance(allowed_tools, str):
        raise SkillPackageValidationError("allowed-tools 必须是字符串")
    return metadata


def build_deterministic_skill_zip(files: dict[str, bytes]) -> bytes:
    """以固定顺序、时间戳和权限构建可复现 ZIP。"""

    output = io.BytesIO()
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(files):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.flag_bits |= 0x800
            archive.writestr(info, files[path], compresslevel=9)
    return output.getvalue()


def validate_skill_zip(content: bytes) -> SkillPackageValidation:
    """不执行也不解压到磁盘地校验一个单 Skill ZIP。"""

    if not content or len(content) > settings.mcpcat_skill_zip_max_bytes:
        raise SkillPackageValidationError("ZIP 大小超过限制或为空")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, OSError) as error:
        raise SkillPackageValidationError("上传内容不是有效 ZIP") from error

    with archive:
        infos = archive.infolist()
        file_infos = [info for info in infos if not info.is_dir()]
        if not file_infos or len(file_infos) > settings.mcpcat_skill_max_files:
            raise SkillPackageValidationError("ZIP 文件数量超过限制或为空")
        total_size = 0
        paths: dict[str, zipfile.ZipInfo] = {}
        root_names = set()
        for info in infos:
            path = _safe_path(info.filename)
            _validate_entry_type(info)
            if not path.parts:
                continue
            root_names.add(path.parts[0])
            normalized = path.as_posix().rstrip("/")
            if normalized in paths:
                raise SkillPackageValidationError("ZIP 包含重复规范化路径")
            paths[normalized] = info
            if info.is_dir():
                continue
            if info.file_size > settings.mcpcat_skill_file_max_bytes:
                raise SkillPackageValidationError("ZIP 中单个文件超过限制")
            total_size += info.file_size
            if total_size > settings.mcpcat_skill_expanded_max_bytes:
                raise SkillPackageValidationError("ZIP 展开总大小超过限制")
        if len(root_names) != 1:
            raise SkillPackageValidationError("ZIP 必须只有一个 Skill 根目录")
        root_name = next(iter(root_names))
        validate_skill_slug(root_name)
        skill_path = f"{root_name}/SKILL.md"
        if skill_path not in paths or paths[skill_path].is_dir():
            raise SkillPackageValidationError("Skill 根目录必须包含 SKILL.md")

        files: dict[str, bytes] = {}
        manifest = []
        scripts = []
        for path, info in sorted(paths.items()):
            if info.is_dir():
                continue
            data = archive.read(info)
            if len(data) != info.file_size:
                raise SkillPackageValidationError("ZIP 文件展开长度不一致")
            files[path] = data
            digest = hashlib.sha256(data).hexdigest()
            manifest.append(SkillFileEntry(path=path, size=len(data), sha256=digest))
            relative = PurePosixPath(path).relative_to(root_name)
            mode = info.external_attr >> 16
            if (
                relative.parts
                and relative.parts[0] == "scripts"
                or relative.suffix.lower() in SCRIPT_SUFFIXES
                or bool(mode & 0o111)
            ):
                scripts.append(path)

        metadata = _parse_frontmatter(files[skill_path], root_name)
        normalized = build_deterministic_skill_zip(files)
        return SkillPackageValidation(
            name=metadata["name"],
            description=metadata["description"].strip(),
            compatibility=metadata.get("compatibility"),
            metadata=metadata,
            files=manifest,
            scripts=sorted(set(scripts)),
            normalized_zip=normalized,
            sha256=hashlib.sha256(normalized).hexdigest(),
        )
