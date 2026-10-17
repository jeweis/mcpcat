"""Registry 到官方 Skills entry/文件的只读适配，无内容执行或共享缓存。"""

import mimetypes
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from app.services.skill_content_service import load_variant
from app.services.skill_resource_uri import (
    SkillResourceIdentity,
    parse_skill_resource_uri,
    skill_resource_uri,
)
from app.services.skill_variant_artifacts import file_media_type, read_manifest_file
from app.storage.database import Database
from app.storage.skill_repositories import SkillDomainError
from app.storage.unit_of_work import UnitOfWork


class RemoteSkillAccessError(SkillDomainError):
    """未授权、未知或已撤销内容对外均不提供 Registry 细节。"""


@dataclass(frozen=True)
class SkillEndpointScope:
    """由网关运行时提供的作用域，不采用客户端指定的 binding。"""

    instance: str
    endpoint: str
    kind: Literal["independent", "service", "catalog"] = "independent"
    binding: str = ""
    source_name: str = ""
    require_auth: bool = True


class RemoteSkillRegistry:
    """每次调用重新授权并验证冻结制品，管理 WRITE 不授予草稿读取。"""

    def __init__(
        self,
        database: Database,
        scope: SkillEndpointScope,
        *,
        authorize: Callable[[], bool],
        available: Callable[[], bool],
        artifact_root: Path | None = None,
        eligible: Callable[[dict], bool] = lambda metadata: True,
    ) -> None:
        if (scope.kind == "independent" and scope.binding) or (
            scope.kind != "independent" and scope.binding != scope.endpoint
        ):
            raise ValueError("端点与变体 binding 不一致")
        self.database, self.scope = database, scope
        self.authorize, self.available = authorize, available
        self.artifact_root = artifact_root
        self.eligible = eligible

    def _authenticated(self) -> bool:
        if not self.available():
            raise RemoteSkillAccessError("Skill 资源不可用")
        authenticated = self.authorize()
        if self.scope.require_auth and not authenticated:
            raise RemoteSkillAccessError("Skill 资源不可用")
        return authenticated

    def check_access(self) -> None:
        """分页无可见项时也检查当前身份和端点可用状态。"""
        self._authenticated()

    def _load(self, identity: SkillResourceIdentity) -> tuple[dict, bytes]:
        authenticated = self._authenticated()
        if (
            identity.instance != self.scope.instance
            or identity.endpoint != self.scope.endpoint
        ):
            raise RemoteSkillAccessError("Skill 资源不可用")
        try:
            info, content = load_variant(
                self.database,
                slug=identity.slug,
                version=identity.version,
                profile="remote",
                binding=self.scope.binding,
                artifact_root=self.artifact_root,
            )
            metadata = info["metadata"]
            if not self.eligible(metadata):
                raise RemoteSkillAccessError("Skill 资源不可用")
            kind = metadata.get("binding_kind", "independent")
            if info["skill_id"] != identity.skill_id or kind != self.scope.kind:
                raise RemoteSkillAccessError("Skill 资源不可用")
            if (
                kind == "service"
                and metadata.get("source_name") != self.scope.source_name
            ):
                raise RemoteSkillAccessError("Skill 资源不可用")
            if not authenticated and not metadata.get("public_remote", False):
                raise RemoteSkillAccessError("Skill 资源不可用")
            return info, content
        except (ValueError, OSError) as error:
            raise RemoteSkillAccessError("Skill 资源不可用") from error

    def _identity(self, uri: str) -> SkillResourceIdentity:
        try:
            return parse_skill_resource_uri(uri)
        except ValueError as error:
            raise RemoteSkillAccessError("Skill 资源不可用") from error

    def _entry(self, identity: SkillResourceIdentity, info: dict) -> dict[str, Any]:
        resources = []
        for file in info["manifest"]["files"]:
            prefix = identity.slug + "/"
            if not file["path"].startswith(prefix):
                raise RemoteSkillAccessError("Skill 资源不可用")
            resources.append(
                {
                    "uri": skill_resource_uri(
                        replace(identity, file=file["path"][len(prefix) :])
                    ),
                    "size": file["size"],
                    "digest": "sha256:" + file["sha256"],
                }
            )
        return {
            "uri": skill_resource_uri(replace(identity, file="SKILL.md")),
            "frontmatter": info["frontmatter"],
            "resources": resources,
        }

    def get_skill(self, uri: str) -> dict[str, Any]:
        """直接查询固定 SKILL.md，既不依赖 list，也不重新解析 latest。"""
        identity = self._identity(uri)
        if identity.file != "SKILL.md":
            raise RemoteSkillAccessError("Skill 资源不可用")
        info, _ = self._load(identity)
        return self._entry(identity, info)

    def list_skills(self) -> list[dict[str, Any]]:
        """每个 Skill 选择当前作用域最新可读版本，保持完整原子清单。"""
        self._authenticated()
        entries = []
        with UnitOfWork(self.database) as unit:
            for skill in unit.skills.list_visible():
                for version in unit.skill_versions.list_for_skill(skill.id):
                    identity = SkillResourceIdentity(
                        self.scope.instance,
                        self.scope.endpoint,
                        skill.id,
                        version.version,
                        skill.slug,
                    )
                    try:
                        entries.append(self.get_skill(skill_resource_uri(identity)))
                    except RemoteSkillAccessError:
                        continue
                    break
        return sorted(entries, key=lambda entry: entry["uri"])

    def read_file(self, uri: str) -> tuple[bytes, str]:
        """正式文件读取不使用 UI 预览限额；原字节及 SHA 必须一致。"""
        identity = self._identity(uri)
        info, content = self._load(identity)
        try:
            data = read_manifest_file(
                content, info["manifest"], identity.slug + "/" + identity.file
            )
        except SkillDomainError as error:
            raise RemoteSkillAccessError("Skill 资源不可用") from error
        return data, file_media_type(identity.file, data)

    def read_directory(self, uri: str) -> list[dict[str, str]]:
        """仅从冻结清单列出直接子项，目录请求不读取任意磁盘路径。"""
        identity = self._identity(uri + "/_directory_probe")
        info, _ = self._load(identity)
        prefix = identity.file.removesuffix("_directory_probe")
        entries: dict[str, dict[str, str]] = {}
        for file in info["manifest"]["files"]:
            path = file["path"].removeprefix(identity.slug + "/")
            if not path.startswith(prefix):
                continue
            child, separator, _ = path[len(prefix) :].partition("/")
            child_uri = skill_resource_uri(replace(identity, file=prefix + child))
            entries[child_uri] = {
                "uri": child_uri,
                "name": child,
                "mimeType": (
                    "inode/directory"
                    if separator
                    else (mimetypes.guess_type(child)[0] or "application/octet-stream")
                ),
            }
        if not entries:
            raise RemoteSkillAccessError("Skill 目录不可用")
        return [entries[key] for key in sorted(entries)]
