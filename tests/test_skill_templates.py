"""静态模板模式、秘密边界、渐进披露和冻结包更新回归。"""

import json
import shutil

import pytest
from test_mcp_skill_generator import (
    _catalog_manager,
    _manager,
    database,
    runtime_patches,
)

from app.core.config import settings
from app.services import mcp_skill_generator as generator
from app.services import skill_template_renderer as renderer
from app.services.skill_package_validator import validate_skill_zip
from app.services.skill_registry_service import publish_skill_version


@pytest.mark.parametrize("kind", ["server", "catalog"])
async def test_local_and_remote_have_same_purpose_but_different_execution(
    runtime_patches, kind
):
    snapshot = await (
        generator.snapshot_catalog(_catalog_manager())
        if kind == "catalog"
        else generator.snapshot_mcp_server(_manager(), "Weather / China")
    )
    from app.services.skill_package_validator import build_deterministic_skill_zip

    packages = {}
    for profile in ("local", "remote"):
        files = generator._package_files(
            slug="demo",
            snapshot=snapshot,
            public_base_url="https://fixture.test",
            profile=profile,
        )
        packages[profile] = validate_skill_zip(build_deterministic_skill_zip(files))
        text = files["demo/SKILL.md"].decode()
        assert len(packages[profile].description) <= generator.DESCRIPTION_MAX_CHARS
        assert packages[profile].metadata["metadata"]["mcpcat-profile"] == profile
        assert packages[profile].metadata["metadata"]["template-version"] == "1.0.0"
        assert "references/tools.md" in text
        for secret in (
            "upstream-token-123",
            "upstream-header-secret",
            "client-secret-value",
            "oauth-access-token",
        ):
            assert all(secret.encode() not in data for data in files.values())
        if profile == "local":
            config = json.loads(files["demo/config/mcporter.json"])
            assert config["imports"] == []
            assert config["mcpServers"]["demo"]["headers"] == {
                "X-Mcpcat-Access": "$env:MCPCAT_API_KEY"
            }
            assert "mcporter@0.13.7" in text
        else:
            assert "demo/config/mcporter.json" not in files
            assert all(
                value not in text
                for value in (
                    "npx",
                    "Node.js",
                    "MCPCAT_API_KEY",
                    "mcporter",
                    "https://fixture.test",
                )
            )
            assert "live" in text and "Host" in text
            if kind == "catalog":
                assert text.index("search_tools") < text.index("call_tool")
                assert "A search result is not authorization" in text
    assert packages["local"].description == packages["remote"].description


def test_source_template_like_text_is_not_executed_or_substituted():
    supplied = "${instructions} $(dangerous-command) {{unsafe-template}}"
    assert supplied in renderer.service_instructions_markdown(supplied)
    snapshot = {
        "source_kind": "server",
        "display_name": "${safety}",
        "instructions": "",
        "tools": [],
    }
    text = renderer.render_skill_body("demo", snapshot, profile="remote")
    assert text.startswith("# ${safety}\n")


async def test_template_change_creates_new_draft_without_rewriting_frozen_zip(
    database, runtime_patches, monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "mcpcat_artifacts_path", str(tmp_path / "artifacts"))
    manager = _manager()
    first = await generator.generate_mcp_skill(
        database, manager=manager, server_name="Weather / China", actor="fixture"
    )
    publish_skill_version(
        database, slug=first.slug, version=first.version, actor="fixture"
    )
    path = tmp_path / "artifacts" / first.slug / (first.version + ".zip")
    original = path.read_bytes()
    templates = tmp_path / "templates"
    shutil.copytree(renderer.TEMPLATE_ROOT, templates)
    template = templates / "local-service.md"
    template.write_text(
        template.read_text() + "\nMaintained service-specific guidance.\n"
    )
    monkeypatch.setattr(renderer, "TEMPLATE_ROOT", templates)
    changed = await generator.generate_mcp_skill(
        database, manager=manager, server_name="Weather / China", actor="fixture"
    )
    assert changed.changed and changed.version != first.version
    assert path.read_bytes() == original
    unchanged = await generator.generate_mcp_skill(
        database, manager=manager, server_name="Weather / China", actor="fixture"
    )
    assert not unchanged.changed and unchanged.version_id == changed.version_id


async def test_description_or_instructions_change_is_part_of_generation_context(
    database, runtime_patches
):
    manager = _manager()
    first = await generator.generate_mcp_skill(
        database, manager=manager, server_name="Weather / China", actor="fixture"
    )
    manager.server_info["Weather / China"]["config"][
        "note"
    ] = "Weather warnings for travel planning"
    second = await generator.generate_mcp_skill(
        database, manager=manager, server_name="Weather / China", actor="fixture"
    )
    assert second.changed and second.tool_schema_hash == first.tool_schema_hash
    manager.server_info["Weather / China"][
        "mcp"
    ].instructions += "\nHandle travel warnings."
    third = await generator.generate_mcp_skill(
        database, manager=manager, server_name="Weather / China", actor="fixture"
    )
    assert third.changed and third.version != second.version
