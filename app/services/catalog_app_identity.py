"""Catalog App 的来源/代际地址；标识本身不是授权凭证。"""

import base64
import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

PREFIX = "ui://mcpcat/catalog-apps/v1/"
MAX_ORIGINAL_URI_BYTES = 8192


def canonical_uuid(value: str) -> str:
    """仅接收规范 UUID，避免别名、大小写或路径字符造成多重解释。"""
    try:
        if str(UUID(value)) == value:
            return value
    except (ValueError, TypeError, AttributeError):
        pass
    raise ValueError("Catalog App identity invalid")


def original_uri(value: str) -> str:
    """原 URI 保持逐字一致，不转换未知 scheme 或把 URI 拼为磁盘路径。"""
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > MAX_ORIGINAL_URI_BYTES
    ):
        raise ValueError("Catalog App resource URI invalid")
    if (
        any(ord(char) < 32 or char.isspace() for char in value)
        or not urlsplit(value).scheme
    ):
        raise ValueError("Catalog App resource URI invalid")
    return value


@dataclass(frozen=True)
class CatalogAppIdentity:
    """持久来源 UUID + 当前运行代际 UUID；同名重建/重启不复用旧回调。"""

    source: str
    generation: str

    def __post_init__(self) -> None:
        canonical_uuid(self.source)
        canonical_uuid(self.generation)

    @property
    def prefix(self) -> str:
        """资源名不依赖可变服务名称、公网地址或客户端先后顺序。"""
        return PREFIX + self.source + "/" + self.generation + "/"

    def resource(self, uri: str) -> str:
        """包裹 URI 而不重写其中 query、fragment、Unicode 或私有协议。"""
        value = original_uri(uri).encode("utf-8")
        return self.prefix + base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    def callback(self, name: str) -> str:
        """不超过旧客户端 64 字符的独立名称，碰撞仍需 Provider 明确拒绝。"""
        if (
            not isinstance(name, str)
            or not name
            or len(name.encode("utf-8")) > 512
            or any(ord(char) < 32 for char in name)
        ):
            raise ValueError("Catalog App tool name invalid")
        digest = hashlib.sha256(
            (self.source + ":" + self.generation + ":" + name).encode()
        ).hexdigest()
        return "catapp_" + digest[:48]

    def unwrap(self, value: str) -> str:
        """只解本来源/代际的规范地址，旧 UI 不能借相似前缀串源。"""
        identity, uri = decode_catalog_resource(value)
        if identity != self:
            raise ValueError("Catalog App source changed")
        return uri


def decode_catalog_resource(value: str) -> tuple[CatalogAppIdentity, str]:
    """严格 Base64URL/UTF-8 解码；编码同义变体、尾部参数和多余段拒绝。"""
    if not isinstance(value, str) or len(value) > 12288 or not value.startswith(PREFIX):
        raise ValueError("Catalog App resource URI invalid")
    parts = value[len(PREFIX) :].split("/")
    if len(parts) != 3 or not re.fullmatch(r"[A-Za-z0-9_-]+", parts[2]):
        raise ValueError("Catalog App resource URI invalid")
    identity = CatalogAppIdentity(parts[0], parts[1])
    try:
        token = parts[2]
        decoded = base64.b64decode(
            token + "=" * (-len(token) % 4), altchars=b"-_", validate=True
        ).decode("utf-8")
        if identity.resource(decoded) != value:
            raise ValueError("noncanonical")
        return identity, decoded
    except (ValueError, UnicodeError):
        raise ValueError("Catalog App resource URI invalid") from None
