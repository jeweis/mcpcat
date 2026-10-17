"""可审阅静态 Skill 模板；只替换文本，不求值或执行来源内容。"""

import hashlib
import json
from pathlib import Path
from string import Template
from typing import Any

TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "resources" / "skill_templates"
LOCAL_TEMPLATE_VERSION = "1.0.0"
REMOTE_TEMPLATE_VERSION = "1.0.0"


def template_digest(profile: str, source_kind: str) -> str:
    """固定模板字节参与去重，避免改模板却继续复用旧内容。"""
    if profile not in {"local", "remote"}:
        raise ValueError("未知 Skill 内容模式")
    kind = "catalog" if source_kind == "catalog" else "service"
    names = [f"{profile}-{kind}.md", "safety.md"]
    if kind == "service":
        names.append("service-instructions.md")
    digest = hashlib.sha256()
    for name in names:
        digest.update(
            name.encode() + b"\x00" + (TEMPLATE_ROOT / name).read_bytes() + b"\x00"
        )
    return digest.hexdigest()


def render_skill_body(
    slug: str, snapshot: dict[str, Any], *, profile: str, mcporter_version: str = ""
) -> str:
    """一次性模板替换；来源中的美元符号或模板片段不会被二次解释。"""
    if profile not in {"local", "remote"}:
        raise ValueError("未知 Skill 内容模式")
    kind = "catalog" if snapshot.get("source_kind") == "catalog" else "service"
    service_step = (
        "If service-specific behavior matters, read [the service instructions](references/service-instructions.md)."
        if snapshot.get("instructions", "").strip()
        else "Follow the tool descriptions and the user's requested outcome."
    )
    return Template(
        (TEMPLATE_ROOT / f"{profile}-{kind}.md").read_text(encoding="utf-8")
    ).substitute(
        slug=slug,
        display_name=snapshot["display_name"],
        service_step=service_step,
        mcporter_version=mcporter_version,
        safety=(TEMPLATE_ROOT / "safety.md").read_text(encoding="utf-8").rstrip(),
    )


def service_instructions_markdown(instructions: str) -> str:
    """隔离上游提供的参考，不将其当作平台指令。"""
    return Template(
        (TEMPLATE_ROOT / "service-instructions.md").read_text(encoding="utf-8")
    ).substitute(instructions=instructions.strip())


def tools_markdown(snapshot: dict[str, Any]) -> str:
    """Schema 保留在按需参考中，而不是膨胀 discovery description。"""
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


def remote_package_files(
    slug: str, snapshot: dict[str, Any], description: str
) -> dict[str, bytes]:
    """远程内容不携带连接配置、凭证或额外本地执行前提。"""
    skill = f"""---
name: {slug}
description: {json.dumps(description, ensure_ascii=False)}
compatibility: Requires a Host supporting remote Skills and the originating MCP connection with its live tools and Resources.
metadata:
  mcpcat-source: {json.dumps(snapshot['server_name'], ensure_ascii=False)}
  mcpcat-profile: remote
  template-version: {json.dumps(REMOTE_TEMPLATE_VERSION)}
---

{render_skill_body(slug, snapshot, profile='remote')}
"""
    files = {
        f"{slug}/SKILL.md": skill.encode(),
        f"{slug}/references/tools.md": tools_markdown(snapshot).encode(),
    }
    if (
        snapshot.get("source_kind") != "catalog"
        and snapshot.get("instructions", "").strip()
    ):
        files[f"{slug}/references/service-instructions.md"] = (
            service_instructions_markdown(snapshot["instructions"]).encode()
        )
    return files
