"""Apps 上传的静态校验；不导入、构建或执行用户文件。"""

import hashlib
import io
import json
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import prefab_ui.components as components
from pydantic import BaseModel, ValidationError

from app.core.config import settings


@dataclass(frozen=True)
class AppPackageValidation:
    """原始上传内容与已校验文件清单。"""

    content_format: str
    entrypoint: str
    files: dict[str, bytes]
    manifest: dict[str, Any]
    original_content: bytes
    sha256: str


def safe_app_path(path: str) -> str:
    """拒绝穿越、绝对路径、Windows 路径和规范化歧义。"""
    if (
        not path
        or "\\" in path
        or "\x00" in path
        or path.startswith("/")
        or re.match(r"^[A-Za-z]:", path)
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise ValueError("App 包含无效路径")
    return PurePosixPath(path).as_posix()


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result or key in {"__proto__", "constructor", "prototype"}:
            raise ValueError("Prefab JSON 包含重复或不安全的键")
        result[key] = value
    return result


def _component_models() -> dict[str, type[BaseModel]]:
    result = {}
    for name in components.__all__:
        model = getattr(components, name)
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            continue
        field = model.model_fields.get("type")
        if field and field.default == name:
            result[name] = model
    return result


def validate_prefab_json(content: bytes) -> dict[str, Any]:
    """有界检查 JSON 结构，复用已安装官方组件模型而非 from_json。"""

    def reject_constant(value: str) -> None:
        raise ValueError("Prefab JSON 不允许非有限数值")

    try:
        wire = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_json_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("Prefab 必须是合法 UTF-8 JSON") from error
    if not isinstance(wire, dict) or not isinstance(wire.get("view"), dict):
        raise ValueError("Prefab 必须包含 view 组件对象")
    header = wire.get("$prefab", {})
    if (
        not isinstance(header, dict)
        or header.get("version", "0.3") != "0.3"
        or wire.get("version", "0.3") != "0.3"
    ):
        raise ValueError("不支持的 Prefab 协议版本")
    if "state" in wire and not isinstance(wire["state"], dict):
        raise ValueError("Prefab state 必须是对象")
    registry = _component_models()
    stack = [(wire, 0, False)]
    visited = 0
    while stack:
        node, depth, is_component = stack.pop()
        visited += 1
        if depth > 64 or visited > 10000:
            raise ValueError("Prefab 组件结构超过限制")
        if isinstance(node, dict):
            node_type = node.get("type")
            if is_component:
                model = registry.get(node_type) if isinstance(node_type, str) else None
                if model is None:
                    raise ValueError("不支持的 Prefab 组件类型")
                try:
                    model.model_validate(node)
                except (ValidationError, TypeError, ValueError) as error:
                    raise ValueError("Prefab 组件字段不合法") from error
            stack.extend(
                (
                    value,
                    depth + 1,
                    key == "view"
                    or (
                        is_component
                        and key in {"children", "child", "fallback", "else"}
                    ),
                )
                for key, value in node.items()
            )
        elif isinstance(node, list):
            stack.extend((value, depth + 1, is_component) for value in node)
    return wire


def _zip_files(content: bytes) -> dict[str, bytes]:
    files = {}
    seen = set()
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) > settings.mcpcat_app_max_files * 2:
                raise ValueError("App 文件数量超过限制")
            for info in infos:
                path = safe_app_path(
                    info.filename.rstrip("/") if info.is_dir() else info.filename
                )
                if path in seen:
                    raise ValueError("App ZIP 包含重复路径")
                seen.add(path)
                mode = stat.S_IFMT(info.external_attr >> 16)
                if mode not in {0, stat.S_IFREG, stat.S_IFDIR} or info.flag_bits & 1:
                    raise ValueError("App ZIP 不允许链接、特殊文件或加密文件")
                if info.is_dir():
                    continue
                total += info.file_size
                if (
                    total > settings.mcpcat_app_max_bytes
                    or len(files) >= settings.mcpcat_app_max_files
                ):
                    raise ValueError("App 展开容量或文件数量超过限制")
                files[path] = archive.read(info)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as error:
        raise ValueError("不是有效的 App ZIP") from error
    return files


def validate_app_package(
    content: bytes, *, filename: str, entrypoint: str | None = None
) -> AppPackageValidation:
    """校验 Prefab JSON、HTML 或资源 ZIP，同时保留原始上传字节。"""
    if not content or len(content) > settings.mcpcat_app_max_bytes:
        raise ValueError("App 上传大小超过限制或为空")
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix == ".zip":
        files = _zip_files(content)
        entry = safe_app_path(entrypoint or "index.html")
    elif suffix in {".html", ".json"}:
        entry = "index.html" if suffix == ".html" else "app.json"
        files = {entry: content}
    else:
        raise ValueError("App 仅支持 HTML、Prefab JSON 或 ZIP")
    if entry not in files:
        raise ValueError("App 入口文件不存在，请明确指定入口")
    if entry.endswith(".json"):
        validate_prefab_json(files[entry])
        content_format = "prefab"
    elif entry.endswith(".html"):
        try:
            files[entry].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("HTML 必须是 UTF-8") from error
        content_format = "html"
    else:
        raise ValueError("App 入口必须是 HTML 或 Prefab JSON")
    manifest = {
        "files": [
            {
                "path": path,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for path, data in sorted(files.items())
        ]
    }
    return AppPackageValidation(
        content_format,
        entry,
        files,
        manifest,
        content,
        hashlib.sha256(content).hexdigest(),
    )
