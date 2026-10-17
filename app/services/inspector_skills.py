"""通过已连接 MCP 客户端检查 Skills；不访问管理数据库或执行资源。"""

import base64
import hashlib
import json
import re
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml
from mcp.types import PaginatedRequestParams, Request, RequestParams, Result
from pydantic import ConfigDict

EXTENSION = "io.modelcontextprotocol/skills"
MAX_BYTES = 16 * 1024 * 1024


class ProtocolResult(Result):
    """保留扩展返回字段，由 Inspector 显式验证。"""

    model_config = ConfigDict(extra="allow")


class ProtocolParams(RequestParams):
    """扩展参数不能被核心 RequestParams 静默丢弃。"""

    model_config = ConfigDict(extra="allow")


class NamedRequest(Request[ProtocolParams, str]):
    """通过 SDK 公开字段为 URI 方法生成 Mcp-Name。"""

    name_param = "uri"


def capabilities(client: Any) -> dict:
    """从 SDK 的已协商信息读取能力，兼容新旧协议时代。"""
    caps = getattr(client, "server_capabilities", None)
    value = caps.model_dump(by_alias=True, exclude_none=True) if caps else {}
    # 不输出任意上游扩展配置或描述，诊断仅保留能力存在性。
    safe = {
        key: {} for key in ("tools", "resources", "prompts", "completions")
        if key in value
    }
    extensions = value.get("extensions", {})
    safe["extensions"] = {
        key: (
            {"directoryRead": item.get("directoryRead") is True}
            if key == EXTENSION
            else {}
        )
        for key, item in extensions.items()
    }
    return {"protocol": getattr(client, "protocol_version", None), "capabilities": safe}


async def request(client: Any, method: str, params: dict) -> dict:
    """SDK 负责每请求元数据、认证和协议封装。"""
    result = await client.session.send_request(
        (NamedRequest if "uri" in params else Request)(
            method=method, params=ProtocolParams(**params)
        ),
        ProtocolResult,
        request_read_timeout_seconds=30,
    )
    value = result.model_dump(by_alias=True, exclude_none=True)
    if len(json.dumps(value).encode()) > MAX_BYTES:
        raise ValueError("协议响应超过 16 MiB")
    if (
        value.get("resultType") != "complete"
        or value.get("cacheScope") not in {"private", "public"}
        or type(value.get("ttlMs")) is not int
    ):
        raise ValueError("扩展响应缺少合法 resultType/ttlMs/cacheScope")
    return value


def require_official(client: Any) -> dict:
    """未观察到官方能力时不发送扩展方法。"""
    caps = capabilities(client)["capabilities"]
    extensions = caps.get("extensions", {})
    if EXTENSION not in extensions or "resources" not in caps:
        raise ValueError("端点未声明标准 Skills 扩展")
    return extensions[EXTENSION]


def validate_entry(entry: dict) -> dict:
    """拒绝不完整静态清单，dynamic 不冒充校验通过。"""
    uri = entry.get("uri")
    frontmatter = entry.get("frontmatter")
    if not isinstance(uri, str) or not uri.endswith("/SKILL.md"):
        raise ValueError("Skill URI 必须指向 SKILL.md")
    if not isinstance(frontmatter, dict) or not all(
        isinstance(frontmatter.get(key), str) and frontmatter[key]
        for key in ("name", "description")
    ):
        raise ValueError("Skill frontmatter 缺少 name/description")
    if unquote(uri.rsplit("/", 2)[-2]) != frontmatter["name"]:
        raise ValueError("Skill 目录名称与 frontmatter.name 不一致")
    files = entry.get("resources")
    if files == "dynamic":
        return entry
    if not isinstance(files, list) or not 1 <= len(files) <= 512:
        raise ValueError("静态清单必须包含 1–512 个文件")
    seen, total = set(), 0
    root = uri.rsplit("/", 1)[0] + "/"
    for file in files:
        path, size, digest = file.get("uri"), file.get("size"), file.get("digest")
        if not isinstance(path, str) or not path.startswith(root):
            raise ValueError("文件不在 Skill 目录中")
        parts = unquote(urlsplit(path).path).split("/")
        if any(part in {".", ".."} for part in parts) or path in seen:
            raise ValueError("清单包含重复或不安全 URI")
        if (
            type(size) is not int
            or size < 0
            or not isinstance(digest, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        ):
            raise ValueError("静态文件缺少合法 size/SHA256")
        total += size
        seen.add(path)
    if uri not in seen or total > MAX_BYTES:
        raise ValueError("清单缺少 SKILL.md 或超过 16 MiB")
    return entry


async def list_skills(client: Any, mode: str, cursor: str | None) -> dict:
    """返回一页完整条目，不自动遍历所有正文。"""
    if mode == "auto":
        caps = capabilities(client)["capabilities"]
        if EXTENSION in caps.get("extensions", {}):
            mode = "official"
        elif "resources" in caps:
            mode = "provider"
        else:
            return {"skills": [], "mode": "auto", "supported": False}
    if mode == "provider":
        result = await client.session.list_resources(
            params=PaginatedRequestParams(cursor=cursor)
        )
        data = result.model_dump(by_alias=True, exclude_none=True)
        return {
            "skills": [
                {
                    "uri": row["uri"],
                    "frontmatter": {
                        "name": row.get("name", row["uri"]),
                        "description": row.get("description", ""),
                    },
                    "resources": "dynamic",
                }
                for row in data.get("resources", [])
                if row["uri"].startswith("skill://")
                and row["uri"].endswith("/SKILL.md")
            ],
            "nextCursor": data.get("nextCursor"),
            "mode": mode,
        }
    require_official(client)
    result = await request(client, "skills/list", {"cursor": cursor} if cursor else {})
    entries = result.get("skills")
    if not isinstance(entries, list) or len(entries) > 512:
        raise ValueError("无效 Skills 分页")
    for entry in entries:
        validate_entry(entry)
    return {**result, "mode": mode}


async def read_directory(
    client: Any, skill_uri: str, uri: str, cursor: str | None = None
) -> dict:
    """目录结果仅作实时观察，不能扩充静态清单。"""
    if require_official(client).get("directoryRead") is not True:
        raise ValueError("端点未声明 directoryRead，未发送目录请求")
    root = skill_uri.rsplit("/", 1)[0]
    if uri != root and not uri.startswith(root + "/"):
        raise ValueError("目录不在所选 Skill 内")
    if any(part in {".", ".."} for part in unquote(urlsplit(uri).path).split("/")):
        raise ValueError("无效目录 URI")
    result = await request(
        client,
        "resources/directory/read",
        {"uri": uri, **({"cursor": cursor} if cursor else {})},
    )
    if not isinstance(result.get("resources"), list) or len(result["resources"]) > 512:
        raise ValueError("目录响应超过限制")
    return result


async def get_skill(client: Any, uri: str, mode: str) -> dict:
    """直接 URI 查询不依赖列表；兼容路径明确读取 Provider _manifest。"""
    if mode == "auto":
        mode = ("official" if EXTENSION in
                capabilities(client)["capabilities"].get("extensions", {})
                else "provider")
    if mode == "provider":
        if not uri.startswith("skill://") or not uri.endswith("/SKILL.md"):
            raise ValueError("Provider URI 必须为 skill://…/SKILL.md")
        root = uri.rsplit("/", 1)[0] + "/"
        raw, _ = await read_bytes(client, root + "_manifest")
        manifest = json.loads(raw)
        files = manifest.get("files", [])
        if not isinstance(files, list) or len(files) > 512:
            raise ValueError("Provider 清单超过限制")
        resources = []
        for file in files:
            path = file["path"]
            if (
                path.startswith("/")
                or ".." in path.split("/")
                or ":" in path
                or "\\" in path
            ):
                raise ValueError("Provider 清单路径无效")
            resources.append(
                {
                    "uri": root + path,
                    "size": file.get("size"),
                    "digest": file.get("hash"),
                }
            )
        return {
            "skill": {"uri": uri, "frontmatter": {}, "resources": resources},
            "mode": mode,
        }
    require_official(client)
    result = await request(client, "skills/get", {"uri": uri})
    entry = validate_entry(result.get("skill", {}))
    if entry["uri"] != uri:
        raise ValueError("skills/get 返回了不同的 URI")
    return {**result, "mode": mode}


async def read_bytes(client: Any, uri: str) -> tuple[bytes, str]:
    """精确保留 UTF-8 文本或 base64 blob 原始字节。"""
    blocks = await client.read_resource(uri)
    if len(blocks) != 1 or str(blocks[0].uri) != uri:
        raise ValueError("资源响应 URI 或数量与请求不一致")
    block = blocks[0]
    text = getattr(block, "text", None)
    raw = (
        text.encode("utf-8")
        if text is not None
        else base64.b64decode(block.blob, validate=True)
    )
    if len(raw) > MAX_BYTES:
        raise ValueError("文件超过 16 MiB")
    return raw, getattr(block, "mime_type", None) or "application/octet-stream"


async def read_file(
    client: Any, skill_uri: str, uri: str, mode: str, held_entry: dict | None = None
) -> dict:
    """核验服务器侧持有的清单；变更需要用户显式刷新而非静默接受。"""
    entry = held_entry or (await get_skill(client, skill_uri, mode))["skill"]
    files = entry["resources"]
    expected = None
    if files == "dynamic":
        if uri != skill_uri:
            directory = await read_directory(client, skill_uri, uri.rsplit("/", 1)[0])
            if not any(
                child.get("uri") == uri and child.get("mimeType") != "inode/directory"
                for child in directory["resources"]
            ):
                raise ValueError("文件不在动态目录当前页中")
    else:
        expected = next((file for file in files if file["uri"] == uri), None)
        if expected is None:
            raise ValueError("文件不在当前 Skill 清单中")
    raw, mime = await read_bytes(client, uri)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    verified = "dynamic" if files == "dynamic" else "provider"
    if mode == "official" and expected:
        if expected["size"] != len(raw) or expected["digest"] != digest:
            raise ValueError("完整性校验失败：size/SHA256 不匹配，请刷新元数据")
        verified = "verified"
    if mode == "official" and uri == skill_uri:
        text = raw.decode("utf-8")
        match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
        if not match or yaml.safe_load(match[1]) != entry["frontmatter"]:
            raise ValueError("SKILL.md frontmatter 与协议元数据不一致")
    try:
        text = raw.decode("utf-8") if len(raw) <= 256 * 1024 else None
    except UnicodeDecodeError:
        text = None
    return {
        "uri": uri,
        "size": len(raw),
        "digest": digest,
        "verification": verified,
        "mime_type": mime,
        "text": text,
    }
