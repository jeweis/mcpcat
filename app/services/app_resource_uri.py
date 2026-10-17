"""版本固定的 UI/附件 URI，实例、端点和 App 身份互相隔离。"""

from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit
from uuid import UUID

from app.services.app_package_validator import safe_app_path
from app.storage.skill_repositories import validate_semver


def _uuid(value: str) -> str:
    if str(UUID(value)) != value:
        raise ValueError("App URI 身份必须为规范 UUID")
    return value


@dataclass(frozen=True)
class AppResourceURI:
    """entry 是派生入口，file 是原始附件，互不占用相同路径。"""

    instance: str
    endpoint: str
    app_id: int
    version: str
    path: str | None = None

    def encode(self) -> str:
        """完全固定版本，不生成 latest、查询凭证或模糊路径。"""
        _uuid(self.instance)
        _uuid(self.endpoint)
        if type(self.app_id) is not int or self.app_id <= 0:
            raise ValueError("App URI 身份编号无效")
        validate_semver(self.version)
        scheme = "ui" if self.path is None else "app-file"
        prefix = f"{scheme}://mcpcat/{self.instance}/{self.endpoint}/{self.app_id}/{quote(self.version, safe='')}"
        if self.path is None:
            return prefix + "/entry"
        safe_app_path(self.path)
        return prefix + "/files/" + quote(self.path, safe="/")

    @classmethod
    def parse(cls, uri: str) -> "AppResourceURI":
        """拒绝编码别名、绝对路径、跨层和 query/fragment。"""
        parts = urlsplit(uri)
        if (
            parts.scheme not in {"ui", "app-file"}
            or parts.netloc != "mcpcat"
            or parts.query
            or parts.fragment
        ):
            raise ValueError("不是本平台 App URI")
        segments = parts.path.split("/")
        if len(segments) < 6 or segments[0] != "":
            raise ValueError("App URI 路径无效")
        _, instance, endpoint, app_id, version, kind, *tail = segments
        if not app_id.isascii() or not app_id.isdigit():
            raise ValueError("App URI 身份编号无效")
        path = None
        if kind == "files" and tail:
            path = unquote("/".join(tail), errors="strict")
        elif kind != "entry" or tail:
            raise ValueError("App URI 资源类型无效")
        result = cls(
            instance, endpoint, int(app_id), unquote(version, errors="strict"), path
        )
        if result.encode() != uri:
            raise ValueError("App URI 必须使用规范编码")
        return result
