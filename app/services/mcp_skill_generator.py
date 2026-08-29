"""从运行中的 MCP 服务生成通用 Agent Skill。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

from fastmcp.server.providers import FastMCPProvider

from app.core.config import settings
from app.services.config_service import ConfigService
from app.services.security_service import security_service
from app.services.skill_artifact_service import store_version_artifact
from app.services.skill_package_validator import (
    build_deterministic_skill_zip,
    validate_skill_zip,
)
from app.storage.database import Database
from app.storage.skill_repositories import SkillDomainError, semver_key
from app.storage.unit_of_work import UnitOfWork

GENERATOR_VERSION = "1.0.1"
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


def _description(snapshot: dict[str, Any]) -> str:
    tool_names = ", ".join(tool["name"] for tool in snapshot["tools"][:12])
    tags = ", ".join(str(tag) for tag in snapshot["tags"][:8])
    parts = [
        f"Use mcpcat MCP service {snapshot['display_name']} through mcporter.",
        f"Use this skill whenever the user needs {snapshot['note'] or tool_names or 'this remote MCP service' }.",
    ]
    if tags:
        parts.append(f"Relevant topics: {tags}.")
    return " ".join(parts)[:1024]


def _tools_markdown(snapshot: dict[str, Any]) -> str:
    sections = [
        "# MCP tools",
        "",
        "Always inspect the live schema before calling a tool.",
        "",
    ]
    for tool in snapshot["tools"]:
        sections.extend(
            [
                f"## `{tool['name']}`",
                "",
                tool["description"] or "No description provided.",
                "",
                "```json",
                json.dumps(tool["inputSchema"], ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )
    return "\n".join(sections)


def _package_files(
    *,
    slug: str,
    snapshot: dict[str, Any],
    public_base_url: str,
) -> dict[str, bytes]:
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
    instructions = snapshot["instructions"].strip()
    skill_md = f"""---
name: {slug}
description: {json.dumps(description, ensure_ascii=False)}
compatibility: Requires Node.js {NODE_COMPATIBILITY}, mcporter {MCPORTER_VERSION}, network access to mcpcat, and MCPCAT_API_KEY when authentication is enabled.
metadata:
  mcpcat-source: {json.dumps(snapshot['server_name'], ensure_ascii=False)}
  mcporter-version: {json.dumps(MCPORTER_VERSION)}
---

# {snapshot['display_name']} via mcpcat

Use only the bundled `config/mcporter.json`; it disables automatic imports and limits calls to the listed tools. Never print, persist, or pass `MCPCAT_API_KEY` as a command argument.

1. Confirm `MCPCAT_URL` if the default instance is not intended and ensure `MCPCAT_API_KEY` is available in the environment when required.
2. Discover live tools with `npx --yes mcporter@{MCPORTER_VERSION} --config config/mcporter.json list {slug} --brief`.
3. Read one live schema with `npx --yes mcporter@{MCPORTER_VERSION} --config config/mcporter.json list {slug}.<tool> --schema`.
4. Call only the user-authorized tool with `npx --yes mcporter@{MCPORTER_VERSION} --config config/mcporter.json call {slug}.<tool> --args '<JSON object>' --output json`.
5. Stop and report safe diagnostics on authentication, connection, or schema errors. Do not rewrite config or start OAuth automatically.

Review [the generated tool reference](references/tools.md) before choosing a tool.

## Service instructions

{instructions or 'No additional service instructions were provided.'}

## Safety

Treat write, delete, send, purchase, publish, or account-changing tools as side effects and obtain clear user intent before calling them. Tool schemas are a snapshot; the live schema is authoritative.
"""
    root = f"{slug}/"
    return {
        f"{root}SKILL.md": skill_md.encode(),
        f"{root}config/mcporter.json": (
            json.dumps(mcporter_config, ensure_ascii=False, indent=2) + "\n"
        ).encode(),
        f"{root}references/tools.md": _tools_markdown(snapshot).encode(),
    }


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
) -> GeneratedSkillResult:
    """使用已规范化来源快照创建或刷新待发布草稿。"""

    schema_hash = _schema_hash(snapshot)

    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug(slug)
        if skill is not None:
            versions = unit.skill_versions.list_for_skill(skill.id)
            if versions:
                latest = versions[0]
                if (
                    latest.tool_schema_hash == schema_hash
                    and latest.generator_version == GENERATOR_VERSION
                    and _same_generation_context(
                        latest.source_snapshot_json or {},
                        snapshot,
                        effective_base_url,
                    )
                ):
                    return GeneratedSkillResult(
                        slug, latest.version, latest.id, False, schema_hash
                    )
            version = _next_version(skill.latest_version)
        else:
            version = "0.1.0"

    files = _package_files(
        slug=slug, snapshot=snapshot, public_base_url=effective_base_url
    )
    package = validate_skill_zip(build_deterministic_skill_zip(files))
    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug(slug)
        if skill is None:
            skill = unit.skills.create(
                slug=slug,
                display_name=snapshot["display_name"],
                description=package.description,
                source_type="mcp-generated",
                source_ref=source_ref,
                created_by=actor,
            )
        version_row = unit.skill_versions.create(
            skill=skill,
            version=version,
            changelog=(
                "Initial generated MCP Skill"
                if skill.latest_version is None
                else "MCP tool schema or generator template changed"
            ),
            source_snapshot={
                **source_ref,
                **_generation_context(snapshot, effective_base_url),
                "source_kind": snapshot["source_kind"],
                "instructions": snapshot["instructions"],
                "tools": snapshot["tools"],
                "files": [entry.__dict__ for entry in package.files],
                "scripts": package.scripts,
            },
            compatibility={
                "node": NODE_COMPATIBILITY,
                "mcporter": MCPORTER_VERSION,
            },
            tool_schema_hash=schema_hash,
            generator_version=GENERATOR_VERSION,
            created_by=actor,
        )
        unit.session.flush()
        version_id = version_row.id
        unit.commit()
    try:
        store_version_artifact(
            database,
            skill_version_id=version_id,
            content=package.normalized_zip,
        )
        with UnitOfWork(database) as unit:
            skill = unit.skills.get_by_slug(slug)
            unit.skill_audit.record(
                skill_id=skill.id,
                skill_slug=slug,
                version=version,
                action="generate",
                actor=actor,
                outcome="success",
                details={"tool_schema_hash": schema_hash},
            )
            unit.commit()
    except Exception:
        with UnitOfWork(database) as unit:
            version_row = unit.skill_versions.get_by_id(version_id)
            if version_row is not None:
                unit.skill_versions.delete_draft(version_row)
                unit.commit()
        raise
    return GeneratedSkillResult(slug, version, version_id, True, schema_hash)


async def generate_mcp_skill(
    database: Database,
    *,
    manager,
    server_name: str,
    actor: Optional[str],
    fallback_base_url: Optional[str] = None,
) -> GeneratedSkillResult:
    """首次生成或在 Schema/模板变化时创建普通 MCP Skill 草稿。"""

    snapshot = await snapshot_mcp_server(manager, server_name)
    return _create_or_refresh_skill(
        database,
        snapshot=snapshot,
        slug=resolve_mcp_skill_slug(database, server_name),
        source_ref={"mcp_server": server_name, "source_status": "available"},
        actor=actor,
        effective_base_url=_effective_base_url(fallback_base_url),
    )


async def generate_catalog_skill(
    database: Database,
    *,
    manager,
    actor: Optional[str],
    fallback_base_url: Optional[str] = None,
) -> GeneratedSkillResult:
    """生成只包含 search_tools / call_tool 的内置 Catalog Skill。"""

    snapshot = await snapshot_catalog(manager)
    return _create_or_refresh_skill(
        database,
        snapshot=snapshot,
        slug=_resolve_catalog_slug(database),
        source_ref={
            "catalog": True,
            "mcp_server": snapshot["server_name"],
            "source_status": "available",
        },
        actor=actor,
        effective_base_url=_effective_base_url(fallback_base_url),
    )
