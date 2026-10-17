"""不可变 Skill 资源身份；URI 只解析为 Registry 键，不作为磁盘路径。"""

from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit
from uuid import UUID

from app.storage.skill_repositories import validate_semver, validate_skill_slug


@dataclass(frozen=True)
class SkillResourceIdentity:
    """实例、端点和明确版本确定唯一的 remote 文件。"""

    instance: str
    endpoint: str
    skill_id: int
    version: str
    slug: str
    file: str = "SKILL.md"


def _segment(value: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("无效 Skill URI 段")
    return quote(value, safe="-._~")


def skill_resource_uri(identity: SkillResourceIdentity) -> str:
    """所有文件均位于与 frontmatter.name 相同的 Skill 目录下。"""
    if str(UUID(identity.instance)) != identity.instance:
        raise ValueError("无效实例身份")
    if str(UUID(identity.endpoint)) != identity.endpoint:
        raise ValueError("无效端点身份")
    if isinstance(identity.skill_id, bool) or identity.skill_id <= 0:
        raise ValueError("无效 Skill 身份")
    validate_semver(identity.version)
    validate_skill_slug(identity.slug)
    parts = (
        identity.instance,
        identity.endpoint,
        str(identity.skill_id),
        identity.version,
        "remote",
        identity.slug,
        *identity.file.split("/"),
    )
    return "skill://mcpcat/" + "/".join(_segment(part) for part in parts)


def parse_skill_resource_uri(uri: str) -> SkillResourceIdentity:
    """只接受规范编码；拒绝查询、碎片、双斜线和编码目录穿越。"""
    if not isinstance(uri, str) or len(uri) > 8192:
        raise ValueError("无效 Skill URI")
    parsed = urlsplit(uri)
    if (
        parsed.scheme != "skill"
        or parsed.netloc != "mcpcat"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("无效 Skill URI 来源")
    raw = parsed.path.split("/")
    if len(raw) < 8 or raw[0] != "":
        raise ValueError("无效 Skill URI 结构")
    parts = [unquote(part, errors="strict") for part in raw[1:]]
    if parts[4] != "remote" or not parts[2].isdigit():
        raise ValueError("无效 Skill URI 模式/身份")
    identity = SkillResourceIdentity(
        instance=parts[0],
        endpoint=parts[1],
        skill_id=int(parts[2]),
        version=parts[3],
        slug=parts[5],
        file="/".join(parts[6:]),
    )
    # 编码后的斜线不能改变目录分段；非规范等价 URI 也不作别名。
    if any("/" in part for part in parts) or skill_resource_uri(identity) != uri:
        raise ValueError("Skill URI 非规范或包含无效路径")
    return identity
