"""从运行中的 MCP 服务生成通用 Agent Skill。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

from fastmcp.server.providers import FastMCPProvider
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.services.config_service import ConfigService
from app.services.security_service import security_service
from app.services.skill_artifact_service import store_version_artifact
from app.services.skill_descriptions import (
    CATALOG_DESCRIPTION,
    DESCRIPTION_MAX_CHARS,
)
from app.services.skill_descriptions import skill_description as _description
from app.services.skill_template_renderer import (
    LOCAL_TEMPLATE_VERSION,
    remote_package_files,
    render_skill_body,
)
from app.services.skill_template_renderer import (
    service_instructions_markdown as _service_instructions_markdown,
)
from app.services.skill_template_renderer import template_digest
from app.services.skill_template_renderer import tools_markdown as _tools_markdown
from app.storage.database import Database
from app.storage.runtime_identities import current_endpoint_identity, endpoint_identity
from app.storage.skill_repositories import SkillDomainError, semver_key
from app.storage.unit_of_work import UnitOfWork

GENERATOR_VERSION = "1.1.0"
MCPORTER_VERSION = "0.13.7"
NODE_COMPATIBILITY = ">=24"
CATALOG_SKILL_SLUG = "mcpcat-tool-search"
CATALOG_DISPLAY_NAME = "mcpcat 工具搜索"


@dataclass(frozen=True)
class GeneratedSkillResult:
    slug: str
    version: str
    version_id: int
    changed: bool
    tool_schema_hash: str


def _slug_candidate(server_name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", server_name.lower()).strip("-")
    if not value:
        value = f"mcp-{hashlib.sha256(server_name.encode()).hexdigest()[:8]}"
    return value[:64].rstrip("-")


def resolve_mcp_skill_slug(database: Database, server_name: str) -> str:
    """稳定映射 MCP 名称；冲突时追加来源哈希。"""

    candidate = _slug_candidate(server_name)
    with UnitOfWork(database) as unit:
        existing = unit.skills.get_by_slug(candidate)
        if existing is None or (
            existing.source_type == "mcp-generated"
            and (existing.source_ref_json or {}).get("mcp_server") == server_name
        ):
            return candidate
        suffix = hashlib.sha256(server_name.encode()).hexdigest()[:8]
        prefix = candidate[: 64 - len(suffix) - 1].rstrip("-") or "mcp"
        resolved = f"{prefix}-{suffix}"
        collision = unit.skills.get_by_slug(resolved)
        if (
            collision is not None
            and (collision.source_ref_json or {}).get("mcp_server") != server_name
        ):
            raise SkillDomainError("无法为 MCP 服务生成无冲突 Skill slug")
        return resolved


def _normalize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_json(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item) for item in value]
    if isinstance(value, set):
        return sorted(_normalize_json(item) for item in value)
    return value


async def snapshot_mcp_server(manager, server_name: str) -> dict[str, Any]:
    """读取运行中 MCP 的 instructions 和 tools/list；失败显式报错。"""

    info = manager.server_info.get(server_name)
    if info is None:
        raise SkillDomainError("MCP 服务不存在")
    if info.get("status") != "running" or info.get("mcp") is None:
        raise SkillDomainError("MCP 服务未运行，无法读取工具 Schema")
    try:
        tools = await FastMCPProvider(info["mcp"]).list_tools()
    except Exception as error:
        raise SkillDomainError("读取 MCP tools/list 失败") from error
    if len(tools) > settings.mcpcat_mcp_skill_max_tools:
        raise SkillDomainError("MCP 工具数量超过 Skill 生成限制")
    normalized_tools = _normalize_tools(tools)
    mcp = info["mcp"]
    return {
        "server_name": server_name,
        "display_name": server_name,
        "source_kind": "server",
        "instructions": getattr(mcp, "instructions", None) or "",
        "tools": normalized_tools,
        "note": (info.get("config") or {}).get("note") or "",
        "tags": list((info.get("config") or {}).get("tags") or []),
        "require_auth": bool((info.get("config") or {}).get("require_auth", True)),
    }


def _normalize_tools(tools) -> list[dict[str, Any]]:
    normalized_tools = []
    for tool in sorted(tools, key=lambda value: value.name):
        payload = tool.model_dump(mode="json")
        normalized_tools.append(
            _normalize_json(
                {
                    "name": payload.get("name"),
                    "description": payload.get("description") or "",
                    "inputSchema": payload.get("parameters") or {},
                    "outputSchema": payload.get("output_schema"),
                    "annotations": payload.get("annotations"),
                }
            )
        )
    return normalized_tools


async def snapshot_catalog(manager) -> dict[str, Any]:
    """读取 Catalog 对外的 search/call 合成工具，而不是展开底层目录。"""

    catalog = getattr(manager, "catalog_service", None)
    if catalog is None or not catalog._config.enabled:
        raise SkillDomainError("Catalog 未启用，无法生成工具搜索 Skill")
    try:
        tools = await catalog.list_external_tools()
    except Exception as error:
        raise SkillDomainError("读取 Catalog 对外工具 Schema 失败") from error
    tool_names = {tool.name for tool in tools}
    if tool_names != {"search_tools", "call_tool"}:
        raise SkillDomainError("Catalog 对外工具契约不完整")
    return {
        "server_name": catalog._config.path_name,
        "display_name": CATALOG_DISPLAY_NAME,
        "source_kind": "catalog",
        "instructions": (
            "Use search_tools to discover relevant tools before call_tool. "
            "A search result is not authorization: confirm clear user intent before "
            "calling tools that write, delete, send, publish, purchase, or change accounts."
        ),
        "tools": _normalize_tools(tools),
        "note": "search for and invoke tools across the mcpcat Catalog",
        "tags": ["mcp", "catalog", "tool-search"],
        "require_auth": bool(catalog._config.require_auth),
    }


def _schema_hash(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"tools": snapshot["tools"]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _skill_body(slug: str, snapshot: dict[str, Any]) -> str:
    return render_skill_body(
        slug, snapshot, profile="local", mcporter_version=MCPORTER_VERSION
    )


def _package_files(
    *,
    slug: str,
    snapshot: dict[str, Any],
    public_base_url: str,
    profile: str = "local",
) -> dict[str, bytes]:
    if profile == "remote":
        return remote_package_files(slug, snapshot, _description(snapshot))
    if profile != "local":
        raise SkillDomainError("未知 Skill 内容模式")
    endpoint = f"${{MCPCAT_URL:-{public_base_url.rstrip('/')}}}/mcp/{quote(snapshot['server_name'], safe='')}"
    auth_header = security_service.get_auth_header_name()
    server_config: dict[str, Any] = {
        "description": f"mcpcat MCP service {snapshot['display_name']}",
        "url": endpoint,
        "allowedTools": [tool["name"] for tool in snapshot["tools"]],
        "lifecycle": "ephemeral",
        "protocolVersion": "auto",
    }
    if snapshot["require_auth"]:
        server_config["headers"] = {auth_header: "$env:MCPCAT_API_KEY"}
    mcporter_config = {
        "$schema": "https://raw.githubusercontent.com/openclaw/mcporter/main/mcporter.schema.json",
        "imports": [],
        "mcpServers": {slug: server_config},
    }
    description = _description(snapshot)
    skill_md = f"""---
name: {slug}
description: {json.dumps(description, ensure_ascii=False)}
compatibility: Requires Node.js {NODE_COMPATIBILITY}, mcporter {MCPORTER_VERSION}, network access to mcpcat, and MCPCAT_API_KEY when authentication is enabled.
metadata:
  mcpcat-source: {json.dumps(snapshot['server_name'], ensure_ascii=False)}
  mcporter-version: {json.dumps(MCPORTER_VERSION)}
  mcpcat-profile: local
  template-version: {json.dumps(LOCAL_TEMPLATE_VERSION)}
---

{_skill_body(slug, snapshot)}
"""
    root = f"{slug}/"
    files = {
        f"{root}SKILL.md": skill_md.encode(),
        f"{root}config/mcporter.json": (
            json.dumps(mcporter_config, ensure_ascii=False, indent=2) + "\n"
        ).encode(),
        f"{root}references/tools.md": _tools_markdown(snapshot).encode(),
    }
    if snapshot.get("source_kind") != "catalog" and snapshot["instructions"].strip():
        files[f"{root}references/service-instructions.md"] = (
            _service_instructions_markdown(snapshot["instructions"]).encode()
        )
    return files


def _next_version(current: Optional[str]) -> str:
    if current is None:
        return "0.1.0"
    key = semver_key(current)
    return f"{key[0]}.{key[1]}.{key[2] + 1}"


def _effective_base_url(fallback_base_url: Optional[str]) -> str:
    resolved_base_url = ConfigService.get_public_base_url() or fallback_base_url
    if not resolved_base_url:
        raise SkillDomainError("无法确定 MCP Skill 的 mcpcat 访问地址")
    return resolved_base_url.rstrip("/")


def _generation_context(snapshot: dict[str, Any], effective_base_url: str) -> dict:
    return {
        "effective_base_url": effective_base_url,
        "endpoint_name": snapshot["server_name"],
        "require_auth": snapshot["require_auth"],
        "template_version": LOCAL_TEMPLATE_VERSION,
        "template_digest": template_digest("local", snapshot["source_kind"]),
        "auth_header_name": security_service.get_auth_header_name(),
        "content_context": hashlib.sha256(
            json.dumps(
                {
                    "description": _description(snapshot),
                    "instructions": snapshot["instructions"],
                    "display_name": snapshot["display_name"],
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest(),
    }


def _same_generation_context(
    latest_snapshot: dict[str, Any],
    snapshot: dict[str, Any],
    effective_base_url: str,
) -> bool:
    latest_endpoint = latest_snapshot.get(
        "endpoint_name", latest_snapshot.get("mcp_server")
    )
    latest_require_auth = latest_snapshot.get("require_auth", snapshot["require_auth"])
    return (
        latest_snapshot.get("effective_base_url") == effective_base_url
        and latest_endpoint == snapshot["server_name"]
        and latest_require_auth == snapshot["require_auth"]
        and all(
            latest_snapshot.get(key) == value
            for key, value in _generation_context(snapshot, effective_base_url).items()
        )
    )


def _resolve_catalog_slug(database: Database) -> str:
    with UnitOfWork(database) as unit:
        existing = unit.skills.get_by_slug(CATALOG_SKILL_SLUG)
        if existing is None:
            return CATALOG_SKILL_SLUG
        source = existing.source_ref_json or {}
        if existing.source_type == "mcp-generated" and source.get("catalog") is True:
            return CATALOG_SKILL_SLUG
    raise SkillDomainError("Skill slug mcpcat-tool-search 已被其他来源占用")


def _create_or_refresh_skill(
    database: Database,
    *,
    snapshot: dict[str, Any],
    slug: str,
    source_ref: dict[str, Any],
    actor: Optional[str],
    effective_base_url: str,
    selection: str = "local",
    source_binding: str | None = None,
) -> GeneratedSkillResult:
    """旧 API 也共用批次保存，不改变默认 local 或结果结构。"""
    from app.services.skill_generation_batch import generate_skill_batch

    return generate_skill_batch(
        database,
        snapshot=snapshot,
        slug=slug,
        source_ref=source_ref,
        actor=actor,
        effective_base_url=effective_base_url,
        selection=selection,
        source_binding=source_binding,
    )


async def generate_mcp_skill(
    database: Database,
    *,
    manager,
    server_name: str,
    actor: Optional[str],
    fallback_base_url: Optional[str] = None,
    selection: str = "local",
) -> GeneratedSkillResult:
    """首次生成或在 Schema/模板变化时创建普通 MCP Skill 草稿。"""

    info = manager.server_info.get(server_name, {})
    if server_name not in manager.server_info:
        raise SkillDomainError("MCP 服务不存在")
    original_mcp = info.get("mcp")
    if info.get("status") != "running" or original_mcp is None:
        raise SkillDomainError("MCP 服务未运行，无法读取工具 Schema")
    binding = endpoint_identity(database, "service", server_name)
    snapshot = await snapshot_mcp_server(manager, server_name)
    if (
        manager.server_info.get(server_name, {}).get("mcp") is not original_mcp
        or manager.server_info.get(server_name, {}).get("status") != "running"
        or current_endpoint_identity(database, "service", server_name) != binding
    ):
        raise SkillDomainError("生成过程中来源已变更，请重新生成")
    return await run_in_threadpool(
        _create_or_refresh_skill,
        database,
        snapshot=snapshot,
        slug=resolve_mcp_skill_slug(database, server_name),
        source_ref={"mcp_server": server_name, "source_status": "available"},
        actor=actor,
        effective_base_url=(
            _effective_base_url(fallback_base_url) if selection != "remote" else ""
        ),
        selection=selection,
        source_binding=binding,
    )


async def generate_catalog_skill(
    database: Database,
    *,
    manager,
    actor: Optional[str],
    fallback_base_url: Optional[str] = None,
    selection: str = "local",
) -> GeneratedSkillResult:
    """生成只包含 search_tools / call_tool 的内置 Catalog Skill。"""

    original_catalog = getattr(manager, "catalog_service", None)
    binding = endpoint_identity(database, "catalog")
    snapshot = await snapshot_catalog(manager)
    if manager.catalog_service is not original_catalog:
        raise SkillDomainError("生成过程中 Catalog 已变更，请重新生成")
    return await run_in_threadpool(
        _create_or_refresh_skill,
        database,
        snapshot=snapshot,
        slug=_resolve_catalog_slug(database),
        source_ref={
            "catalog": True,
            "mcp_server": snapshot["server_name"],
            "source_status": "available",
        },
        actor=actor,
        effective_base_url=(
            _effective_base_url(fallback_base_url) if selection != "remote" else ""
        ),
        selection=selection,
        source_binding=binding,
    )
