"""FastMCP 公开资源 Provider，逐次读取冻结版本且拒绝草稿。"""

import mimetypes
from collections.abc import Sequence
from dataclasses import replace

from fastmcp.apps import UI_MIME_TYPE
from fastmcp.exceptions import ResourceError
from fastmcp.resources import Resource, ResourceContent, ResourceResult
from fastmcp.server.providers import Provider

from app.services.app_resource_registry import AppResourceRegistry


class AppResourcesProvider(Provider):
    """资源对象不是授权快照，read() 仍重新进入 Registry。"""

    def __init__(self, registry: AppResourceRegistry) -> None:
        super().__init__()
        self.registry = registry

    def _resource(
        self, uri: str, *, name: str, path: str | None, metadata: dict | None = None
    ) -> Resource:
        async def read() -> ResourceResult:
            try:
                if path is None:
                    html, _, meta = await self.registry.entry(uri)
                    return ResourceResult(
                        [ResourceContent(html, mime_type=UI_MIME_TYPE, meta=meta)]
                    )
                data = await self.registry.file(uri)
                mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
                try:
                    value = data.decode("utf-8")
                except UnicodeDecodeError:
                    value = data
                return ResourceResult([ResourceContent(value, mime_type=mime)])
            except (ValueError, OSError):
                raise ResourceError("App resource unavailable") from None

        return Resource.from_function(
            read,
            uri,
            name=name,
            mime_type=(
                UI_MIME_TYPE
                if path is None
                else mimetypes.guess_type(path)[0] or "application/octet-stream"
            ),
            meta=metadata,
        )

    async def _get_resource(self, uri: str, version=None) -> Resource | None:
        """UI/附件 URI 独立查找，不依赖此前列出的权限或 latest。"""
        if not uri.startswith(("ui://mcpcat/", "app-file://mcpcat/")):
            return None
        try:
            identity, slug, info, _ = await self.registry.load(uri)
            if identity.path is None:
                _, _, metadata = await self.registry.entry(uri)
                return self._resource(
                    uri, name=f"{slug} {identity.version}", path=None, metadata=metadata
                )
            if not any(
                item["path"] == identity.path for item in info["manifest"]["files"]
            ):
                return None
            return self._resource(uri, name=identity.path, path=identity.path)
        except (ValueError, OSError):
            return None

    async def _list_resources(self) -> Sequence[Resource]:
        """只发布经过当前资格检查的资源目录，不泄露未授权内容。"""
        try:
            entries = self.registry.published()
        except (ValueError, OSError):
            return []
        result = []
        for entry in entries:
            resource = await self._get_resource(entry.encode())
            if resource is not None:
                result.append(resource)
            try:
                _, _, info, _ = await self.registry.load(entry.encode())
                for file in info["manifest"]["files"]:
                    uri = replace(entry, path=file["path"]).encode()
                    result.append(
                        self._resource(uri, name=file["path"], path=file["path"])
                    )
            except (ValueError, OSError):
                continue
        try:
            self.registry.check()
        except ValueError:
            return []
        return result
