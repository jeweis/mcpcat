"""Skills/Apps 共用的不可变制品读取，不解释或执行内容。"""

import hashlib
from pathlib import Path


class ArtifactReadError(ValueError):
    """只保留安全错误类别，不回显路径或读取出的字节。"""


def read_verified_artifact(
    root: Path,
    *,
    relative_path: str,
    size: int,
    sha256: str,
    max_bytes: int | None = None,
) -> bytes:
    """存储边界、size 和原始 SHA 必须同时满足，不解压包。"""
    root = root.resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ArtifactReadError("path_invalid")
    if size < 0 or (max_bytes is not None and size > max_bytes):
        raise ArtifactReadError("integrity")
    if path.stat().st_size != size:
        raise ArtifactReadError("integrity")
    content = path.read_bytes()
    if len(content) != size or hashlib.sha256(content).hexdigest() != sha256:
        raise ArtifactReadError("integrity")
    return content
