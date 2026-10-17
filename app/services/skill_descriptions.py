"""本地与远程共享的用途/触发描述，不包含连接技术实现。"""

import re
from typing import Any

DESCRIPTION_MAX_CHARS = 420
CATALOG_DESCRIPTION = (
    "Search available tools and invoke the appropriate one. Use when the user "
    "asks what tools are available, needs a capability that is not already mapped "
    "to a specific service, or wants help discovering a tool for a task."
)
TECHNICAL_TAGS = frozenset(
    {
        "api",
        "catalog",
        "http",
        "https",
        "mcp",
        "mcpcat",
        "mcporter",
        "openapi",
        "sse",
        "stdio",
        "streamable-http",
        "tool-search",
    }
)


def _clean_prose(value: Any, *, limit: int = 240) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(normalized) <= limit:
        return normalized
    candidate = normalized[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:.")
    return f"{candidate or normalized[:limit - 1]}…"


def _as_sentence(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    if value[-1] not in ".!?。！？":
        value += "."
    return value[0].upper() + value[1:] if value[0].isascii() else value


def _business_tags(snapshot: dict[str, Any]) -> list[str]:
    result = []
    for value in snapshot.get("tags") or []:
        tag = _clean_prose(value, limit=48).strip(" ,;:.")
        if not tag or tag.casefold() in TECHNICAL_TAGS:
            continue
        if tag.casefold() not in {item.casefold() for item in result}:
            result.append(tag)
    return result[:4]


def _tool_capabilities(snapshot: dict[str, Any]) -> list[str]:
    result = []
    for tool in snapshot.get("tools") or []:
        capability = _clean_prose(tool.get("description"), limit=180)
        if not capability or capability.casefold() == "no description provided.":
            continue
        capability = _as_sentence(capability)
        if capability.casefold() not in {item.casefold() for item in result}:
            result.append(capability)
        if len(result) == 2:
            break
    return result


def skill_description(snapshot: dict[str, Any]) -> str:
    """保持用途与触发场景相同；profile 前提由 renderer 单独说明。"""
    if snapshot.get("source_kind") == "catalog":
        return CATALOG_DESCRIPTION
    note = _clean_prose(snapshot.get("note"))
    capabilities = [_as_sentence(note)] if note else _tool_capabilities(snapshot)
    if not capabilities:
        capabilities = [
            f"Provides the documented capabilities of {snapshot['display_name']}."
        ]
    tags = _business_tags(snapshot)
    trigger = (
        f"Use when the user asks about {', '.join(tags)} or needs one of these documented capabilities."
        if tags
        else "Use when the user's request needs one of these documented capabilities, even if they do not name the Skill explicitly."
    )
    capability_budget = DESCRIPTION_MAX_CHARS - len(trigger) - 1
    capability_text = _clean_prose(
        " ".join(capabilities), limit=max(capability_budget, 80)
    )
    return f"{capability_text} {trigger}"
