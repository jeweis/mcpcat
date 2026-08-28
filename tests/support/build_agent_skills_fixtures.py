"""为官方 skills-ref Gate B 验收生成实际上传、MCP 与 bundle fixtures。"""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

from app.services import mcp_skill_generator
from app.services.skill_package_validator import (
    build_deterministic_skill_zip,
    validate_skill_zip,
)


def _extract(content: bytes, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    archive_path = target / "fixture.zip"
    archive_path.write_bytes(content)
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(target)
    archive_path.unlink()


def main(output: Path) -> None:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    uploaded_files = {
        "uploaded-example/SKILL.md": b"""---
name: uploaded-example
description: Review uploaded example data. Use when the user asks to inspect an uploaded fixture.
compatibility: Requires a Skills-compatible agent.
metadata:
  fixture: gate-b
---

# Uploaded fixture

Read `references/example.md` when detailed context is required.
""",
        "uploaded-example/references/example.md": b"# Example\n",
    }
    uploaded = validate_skill_zip(build_deterministic_skill_zip(uploaded_files))
    _extract(uploaded.normalized_zip, output / "uploaded")

    original_header = mcp_skill_generator.security_service.get_auth_header_name
    mcp_skill_generator.security_service.get_auth_header_name = lambda: "Mcpcat-Key"
    try:
        generated_files = mcp_skill_generator._package_files(
            slug="generated-example",
            snapshot={
                "server_name": "fixture-server",
                "instructions": "Use deterministic fixture tools.",
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo one message.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"message": {"type": "string"}},
                            "required": ["message"],
                        },
                        "outputSchema": {"type": "object"},
                        "annotations": {"readOnlyHint": True},
                    }
                ],
                "note": "fixture",
                "tags": ["test"],
                "require_auth": True,
            },
            public_base_url="https://mcpcat.example",
        )
    finally:
        mcp_skill_generator.security_service.get_auth_header_name = original_header
    generated = validate_skill_zip(build_deterministic_skill_zip(generated_files))
    _extract(generated.normalized_zip, output / "generated")

    bundle_files = {**uploaded_files, **generated_files}
    bundle_files[".mcpcat-bundle.json"] = json.dumps(
        {
            "schema_version": "1.0.0",
            "bundle_id": "fixture",
            "registry": "https://mcpcat.example",
            "generated_at": "2026-08-29T00:00:00+00:00",
            "skills": [
                {"slug": "uploaded-example", "version": "1.0.0"},
                {"slug": "generated-example", "version": "0.1.0"},
            ],
        },
        sort_keys=True,
    ).encode()
    _extract(build_deterministic_skill_zip(bundle_files), output / "bundle")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
