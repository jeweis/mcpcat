"""包内静态资产解析：只查已验证字节，不访问网络或宿主文件。"""

import base64
import posixpath
from urllib.parse import unquote, urlsplit

from app.services.app_content_service import AppDomainError
from app.services.app_package_validator import safe_app_path


class AppAssetResolver:
    """相对路径按引用文件解析，外部来源必须在审核列表内。"""

    def __init__(self, files: dict[str, bytes], resource_domains: list[str]) -> None:
        self.files = files
        self.resource_domains = set(resource_domains)
        self.generated_bytes = 0

    def local_path(self, reference: str, base: str) -> str | None:
        """不把上传路径拼到真实磁盘；包内 ../ 只允许在包边界内。"""
        if (
            any(char.isspace() or ord(char) < 32 for char in reference)
            or "\\" in reference
        ):
            raise AppDomainError("App 资产引用包含非法字符")
        parsed = urlsplit(reference)
        if parsed.scheme or parsed.netloc:
            if parsed.scheme == "data" and not parsed.netloc:
                return None
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if (
                parsed.scheme != "https"
                or parsed.username
                or parsed.password
                or origin not in self.resource_domains
            ):
                raise AppDomainError("App 外部资源来源未经审核")
            return None
        if not parsed.path:
            return None
        path = unquote(parsed.path, errors="strict")
        path = posixpath.normpath(
            path.lstrip("/")
            if path.startswith("/")
            else posixpath.join(posixpath.dirname(base), path)
        )
        safe_app_path(path)
        if path not in self.files:
            raise AppDomainError("App 引用的包内附件不存在")
        return path

    def data_uri(self, reference: str, base: str) -> str:
        """返回内容地址，不从 URI 下载或执行任何资产。"""
        import mimetypes

        path = self.local_path(reference, base)
        if path is None:
            return reference
        media = mimetypes.guess_type(path)[0] or "application/octet-stream"
        projected = (
            ((len(self.files[path]) + 2) // 3) * 4 + len(media) + len(reference) + 32
        )
        if self.generated_bytes + projected > 64 * 1024 * 1024:
            raise AppDomainError("App 派生资产超过容量限制")
        self.generated_bytes += projected
        data = base64.b64encode(self.files[path]).decode("ascii")
        fragment = urlsplit(reference).fragment
        return f"data:{media};base64,{data}" + (f"#{fragment}" if fragment else "")
