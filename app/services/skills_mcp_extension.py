"""固定 FastMCP 4.1 公开扩展/Provider 的 Skills 协议适配。"""

import inspect
import mimetypes
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Any

from fastmcp.resources import Resource, ResourceContent, ResourceResult
from fastmcp.resources.function_resource import FunctionResource
from fastmcp.server.extensions import MethodBinding, ServerExtension
from fastmcp.server.providers import Provider
from fastmcp.utilities.versions import VersionSpec
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp.types import RequestParams
from mcp_types import INVALID_PARAMS, METHOD_NOT_FOUND

from app.services.remote_skill_registry import (
    RemoteSkillAccessError,
    RemoteSkillRegistry,
)
from app.services.skill_listing_cursors import SkillListingCursors
from app.services.skill_resource_uri import parse_skill_resource_uri, skill_resource_uri

SKILLS_EXTENSION = "io.modelcontextprotocol/skills"
MODERN_PROTOCOL = "2026-07-28"
RegistryFactory = Callable[[], RemoteSkillRegistry | Awaitable[RemoteSkillRegistry]]


async def resolve_registry(factory: RegistryFactory) -> RemoteSkillRegistry:
    """允许来源适配先用公开 Provider 读取当前可用工具。"""
    value = factory()
    return await value if inspect.isawaitable(value) else value


async def read_registry_resource(factory: RegistryFactory, uri: str) -> ResourceResult:
    """共享资源读取入口，来源中间件不能让上游同名资源覆盖 Registry。"""
    try:
        registry = await resolve_registry(factory)
        data, media_type = registry.read_file(uri)
    except (ValueError, OSError) as error:
        raise MCPError(INVALID_PARAMS, "Unknown Skill resource") from error
    try:
        value = data.decode("utf-8")
    except UnicodeDecodeError:
        value = data
    return ResourceResult([ResourceContent(value, mime_type=media_type)])


class ListSkillsParams(RequestParams):
    """分页是完整 entry 的分页，而不是文件清单分页。"""

    cursor: str | None = None


class GetSkillParams(RequestParams):
    """直接查 SKILL.md URI。"""

    uri: str


class ReadSkillDirectoryParams(GetSkillParams):
    """目录只列直接子项，游标不能跨目录或身份使用。"""

    cursor: str | None = None


def _cacheable(**fields: Any) -> dict[str, Any]:
    return {"resultType": "complete", "ttlMs": 0, "cacheScope": "private", **fields}


class RegistrySkillsExtension(ServerExtension):
    """能力仅由运行时显式启用，不自动接收上游扩展。"""

    identifier = SKILLS_EXTENSION

    def __init__(
        self,
        registry: RegistryFactory,
        *,
        principal: Callable[[], str],
        page_size: int = 25,
    ) -> None:
        if not 1 <= page_size <= 100:
            raise ValueError("Skills 分页大小必须为 1-100")
        self.registry, self.principal, self.page_size = registry, principal, page_size
        self.cursors = SkillListingCursors()

    def settings(self) -> dict[str, Any]:
        """声明已实现的目录读取，旧协议仍由框架过滤扩展。"""
        return {"directoryRead": True}

    def methods(self) -> Sequence[MethodBinding]:
        """legacy 不注册可用方法，能力序列化由框架时代过滤。"""
        versions = frozenset({MODERN_PROTOCOL})
        return (
            MethodBinding("skills/list", ListSkillsParams, self.list_skills, versions),
            MethodBinding("skills/get", GetSkillParams, self.get_skill, versions),
            MethodBinding(
                "resources/directory/read",
                ReadSkillDirectoryParams,
                self.read_directory,
                versions,
            ),
        )

    async def _registry(self, context: ServerRequestContext) -> RemoteSkillRegistry:
        if self.client_settings(context) is None:
            raise MCPError(METHOD_NOT_FOUND, "Skills extension was not negotiated")
        return await resolve_registry(self.registry)

    async def list_skills(
        self, context: ServerRequestContext, params: ListSkillsParams
    ) -> dict:
        """快照游标不因新版本发布重复/漏页，已撤销项仍立即过滤。"""
        registry = await self._registry(context)
        owner = (
            registry.scope.instance
            + ":"
            + registry.scope.endpoint
            + ":"
            + self.principal()
        )
        try:
            if params.cursor:
                snapshot = self.cursors.get(params.cursor, owner)
                uris, position = snapshot.uris, snapshot.position
            else:
                uris = tuple(entry["uri"] for entry in registry.list_skills())
                position = 0
            entries = []
            # 续页也必须验证端点/身份，即使所有 URI 都已撤销。
            registry.check_access()
            while position < len(uris) and len(entries) < self.page_size:
                uri, position = uris[position], position + 1
                try:
                    entries.append(registry.get_skill(uri))
                except RemoteSkillAccessError:
                    continue
            result = _cacheable(skills=entries)
            if position < len(uris):
                result["nextCursor"] = self.cursors.save(uris, position, owner)
            return result
        except (ValueError, OSError) as error:
            raise MCPError(INVALID_PARAMS, "Skills listing is unavailable") from error

    async def get_skill(
        self, context: ServerRequestContext, params: GetSkillParams
    ) -> dict:
        """未知、越权、撤销 URI 统一使用官方 Invalid params。"""
        registry = await self._registry(context)
        try:
            return _cacheable(skill=registry.get_skill(params.uri))
        except (ValueError, OSError) as error:
            raise MCPError(INVALID_PARAMS, "Unknown Skill URI") from error

    async def read_directory(
        self, context: ServerRequestContext, params: ReadSkillDirectoryParams
    ) -> dict:
        """每页重新校验不可变清单及权限，不把游标当访问授权。"""
        registry = await self._registry(context)
        owner = "directory:" + self.principal() + ":" + params.uri
        try:
            entries = registry.read_directory(params.uri)
            by_uri = {entry["uri"]: entry for entry in entries}
            if params.cursor:
                snapshot = self.cursors.get(params.cursor, owner)
                uris, position = snapshot.uris, snapshot.position
            else:
                uris, position = tuple(by_uri), 0
            stop = min(position + self.page_size, len(uris))
            result = _cacheable(resources=[by_uri[uri] for uri in uris[position:stop]])
            if stop < len(uris):
                result["nextCursor"] = self.cursors.save(uris, stop, owner)
            return result
        except (ValueError, OSError, KeyError) as error:
            raise MCPError(INVALID_PARAMS, "Unknown Skill directory") from error


class RegistrySkillResource(FunctionResource):
    """只为网关自有 Skill 元数据使用规范命名空间，不改写上游资源。"""

    def get_meta(self) -> dict[str, Any]:
        """保留框架 tags/version；不使用无命名空间的 fastmcp wire key。"""
        meta = super().get_meta()
        meta["io.modelcontextprotocol.skills/fastmcp"] = meta.pop("fastmcp")
        return meta


class RegistrySkillResources(Provider):
    """Resources 支持普通读取，执行时再次检查授权而非相信发现结果。"""

    def __init__(self, registry: RegistryFactory) -> None:
        super().__init__()
        self.registry = registry

    def _resource(self, uri: str, entry: dict) -> Resource:
        async def read() -> ResourceResult:
            return await read_registry_resource(self.registry, uri)

        identity = parse_skill_resource_uri(uri)
        root = identity.file == "SKILL.md"
        return RegistrySkillResource.from_function(
            read,
            uri,
            name=entry["frontmatter"]["name"] if root else identity.file,
            description=entry["frontmatter"]["description"] if root else None,
            mime_type=mimetypes.guess_type(identity.file)[0]
            or "application/octet-stream",
        )

    async def _list_resources(self) -> Sequence[Resource]:
        """完整元数据资源发现不把正文放入列表。"""
        registry = await resolve_registry(self.registry)
        try:
            entries = registry.list_skills()
        except RemoteSkillAccessError:
            return []
        return [
            self._resource(file["uri"], entry)
            for entry in entries
            for file in entry["resources"]
        ]

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        """不依赖列表，允许直接读取历史固定版本，不能读取非清单文件。"""
        if not uri.startswith("skill://mcpcat/"):
            return None
        try:
            identity = parse_skill_resource_uri(uri)
            registry = await resolve_registry(self.registry)
            entry = registry.get_skill(
                skill_resource_uri(replace(identity, file="SKILL.md"))
            )
            if any(file["uri"] == uri for file in entry["resources"]):
                return self._resource(uri, entry)
        except (ValueError, OSError):
            pass
        raise MCPError(INVALID_PARAMS, "Unknown Skill resource")
