"""MCP Skill 生成器的运行时快照、安全制品与草稿刷新测试。"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.services import mcp_skill_generator as generator
from app.services import skill_artifact_service
from app.storage.database import Database
from app.storage.migrations import upgrade_schema
from app.storage.skill_repositories import SkillDomainError
from app.storage.unit_of_work import UnitOfWork


@dataclass
class FakeTool:
    """提供生成器所需的 FastMCP Tool 最小接口。"""

    name: str
    description: str = "Retrieve a forecast for a city."
    parameters: dict | None = None

    def model_dump(self, *, mode: str) -> dict:
        assert mode == "json"
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters or {"type": "object"},
            "output_schema": {"type": "object"},
            "annotations": {"readOnlyHint": True},
        }


class FakeRunningFastMCP:
    def __init__(self, instructions: str, tools: list[FakeTool]) -> None:
        self.instructions = instructions
        self.tools = tools


class FakeFastMCPProvider:
    """只从传入的运行中 FastMCP 对象读取工具，不访问源配置。"""

    def __init__(self, mcp: FakeRunningFastMCP) -> None:
        self._mcp = mcp

    async def list_tools(self) -> list[FakeTool]:
        return self._mcp.tools


class FakeCatalogService:
    """模拟只暴露 search_tools / call_tool 的内置 Catalog。"""

    def __init__(self, *, path_name: str = "mcpcat", enabled: bool = True) -> None:
        self._config = SimpleNamespace(
            path_name=path_name,
            enabled=enabled,
            require_auth=True,
        )
        self.membership_revision = 1

    async def list_external_tools(self) -> list[FakeTool]:
        return [
            FakeTool(
                "search_tools",
                "Search for tools using natural language.",
                {"type": "object", "properties": {"query": {"type": "string"}}},
            ),
            FakeTool(
                "call_tool",
                "Call a tool discovered through search_tools.",
                {"type": "object", "properties": {"name": {"type": "string"}}},
            ),
        ]


@pytest.fixture
def database(tmp_path: Path) -> Database:
    value = Database(tmp_path / "mcp-skill-generator.db")
    upgrade_schema(value)
    yield value
    value.dispose()


@pytest.fixture
def runtime_patches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(generator, "FastMCPProvider", FakeFastMCPProvider)
    monkeypatch.setattr(
        generator.ConfigService,
        "get_public_base_url",
        staticmethod(lambda: "https://skills.example.test/mcpcat/"),
    )
    monkeypatch.setattr(
        generator.security_service,
        "get_auth_header_name",
        lambda: "X-Mcpcat-Access",
    )
    monkeypatch.setattr(
        generator,
        "store_version_artifact",
        lambda database, *, skill_version_id, content: (
            skill_artifact_service.store_version_artifact(
                database,
                skill_version_id=skill_version_id,
                content=content,
                artifact_root=tmp_path / "artifacts",
            )
        ),
    )


def _manager(
    *,
    server_name: str = "Weather / China",
    transport_type: str = "streamable-http",
    tools: list[FakeTool] | None = None,
    status: str = "running",
) -> SimpleNamespace:
    # 这些字段模拟真实上游配置；生成器不得将它们写入生成包。
    upstream_config = {
        "type": transport_type,
        "url": "https://upstream-secret.example.test/mcp?token=upstream-token-123",
        "command": "python",
        "args": ["-m", "private_upstream"],
        "headers": {"X-Upstream-Secret": "upstream-header-secret"},
        "oauth": {
            "client_id": "client-id",
            "client_secret": "client-secret-value",
            "access_token": "oauth-access-token",
        },
        "note": "weather forecasts",
        "tags": ["weather", "china"],
        "require_auth": True,
    }
    return SimpleNamespace(
        server_info={
            server_name: {
                "status": status,
                "mcp": FakeRunningFastMCP(
                    "Use only after the user asks for a weather forecast.",
                    tools or [FakeTool("forecast")],
                ),
                "config": upstream_config,
            }
        }
    )


def _catalog_manager(*, path_name: str = "mcpcat", enabled: bool = True):
    return SimpleNamespace(
        server_info={},
        catalog_service=FakeCatalogService(path_name=path_name, enabled=enabled),
    )


def _frontmatter(skill_md: str) -> dict:
    return yaml.safe_load(skill_md.split("---", 2)[1])


def test_description_uses_capabilities_and_trigger_context_not_runtime_details() -> (
    None
):
    snapshot = {
        "display_name": "Weather / China",
        "source_kind": "server",
        "note": "",
        "tags": ["mcp", "weather", "streamable-http", "china"],
        "tools": [
            {"name": "forecast", "description": "Retrieve forecasts by city."},
            {"name": "alerts", "description": "List active weather alerts."},
        ],
    }

    description = generator._description(snapshot)

    assert "Retrieve forecasts by city." in description
    assert "List active weather alerts." in description
    assert "Use when" in description
    assert "weather" in description
    assert "china" in description
    for runtime_detail in ("mcpcat", "mcporter", "streamable-http", "/mcp/"):
        assert runtime_detail not in description.casefold()

    long_snapshot = {
        **snapshot,
        "note": "",
        "tools": [
            {
                "name": "verbose",
                "description": "A very detailed user-facing capability " * 80,
            }
        ],
    }
    bounded = generator._description(long_snapshot)
    assert len(bounded) <= generator.DESCRIPTION_MAX_CHARS
    assert "Use when" in bounded


def test_catalog_description_covers_discovery_without_connection_details() -> None:
    description = generator._description(
        {
            "source_kind": "catalog",
            "display_name": generator.CATALOG_DISPLAY_NAME,
            "note": "ignored implementation note",
            "tags": ["mcp", "catalog", "tool-search"],
            "tools": [],
        }
    )

    assert "Search available tools" in description
    assert "Use when" in description
    assert "not already mapped to a specific service" in description
    for runtime_detail in ("mcpcat", "mcporter", "/mcp/"):
        assert runtime_detail not in description.casefold()


def test_slug_conflict_uses_server_hash_without_overwriting(database: Database) -> None:
    with UnitOfWork(database) as unit:
        unit.skills.create(
            slug="weather-china",
            display_name="Manually uploaded weather skill",
            description="Existing unrelated Skill",
            source_type="uploaded",
        )
        unit.commit()

    server_name = "Weather / China"
    expected = "weather-china-" + hashlib.sha256(server_name.encode()).hexdigest()[:8]
    assert generator.resolve_mcp_skill_slug(database, server_name) == expected


async def test_snapshot_reads_running_fastmcp_instructions_and_tools(
    runtime_patches: None,
) -> None:
    manager = _manager(tools=[FakeTool("zeta"), FakeTool("alpha")])

    snapshot = await generator.snapshot_mcp_server(manager, "Weather / China")

    assert (
        snapshot["instructions"]
        == "Use only after the user asks for a weather forecast."
    )
    assert [tool["name"] for tool in snapshot["tools"]] == ["alpha", "zeta"]
    assert snapshot["tools"][0]["inputSchema"] == {"type": "object"}
    assert snapshot["note"] == "weather forecasts"
    assert snapshot["tags"] == ["weather", "china"]


@pytest.mark.parametrize(
    "transport_type", ["stdio", "sse", "streamable-http", "openapi"]
)
async def test_every_transport_generates_only_mcpcat_runtime_package(
    database: Database,
    runtime_patches: None,
    transport_type: str,
    tmp_path: Path,
) -> None:
    server_name = "Weather / China"
    result = await generator.generate_mcp_skill(
        database,
        manager=_manager(server_name=server_name, transport_type=transport_type),
        server_name=server_name,
        actor="admin",
    )

    archive_path = tmp_path / "artifacts" / result.slug / "0.1.0.zip"
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == [
            f"{result.slug}/SKILL.md",
            f"{result.slug}/config/mcporter.json",
            f"{result.slug}/references/service-instructions.md",
            f"{result.slug}/references/tools.md",
        ]
        skill_md = archive.read(f"{result.slug}/SKILL.md").decode()
        service_instructions = archive.read(
            f"{result.slug}/references/service-instructions.md"
        ).decode()
        config = json.loads(
            archive.read(f"{result.slug}/config/mcporter.json").decode()
        )
        package_text = "\n".join(
            archive.read(name).decode(errors="replace") for name in archive.namelist()
        )

    assert "mcporter 0.13.7" in skill_md
    assert "mcporter@0.13.7" in skill_md
    metadata = _frontmatter(skill_md)
    assert metadata["description"].startswith("Weather forecasts.")
    assert "Use when" in metadata["description"]
    assert "weather" in metadata["description"]
    assert "china" in metadata["description"]
    assert "mcpcat" not in metadata["description"].casefold()
    assert "mcporter" not in metadata["description"].casefold()
    assert "Use only after the user asks" not in skill_md
    assert "Use only after the user asks" in service_instructions
    assert "does not override user intent" in service_instructions
    assert config["imports"] == []
    server_config = config["mcpServers"][result.slug]
    assert server_config["url"] == (
        "${MCPCAT_URL:-https://skills.example.test/mcpcat}/mcp/Weather%20%2F%20China"
    )
    assert server_config["allowedTools"] == ["forecast"]
    assert server_config["headers"] == {"X-Mcpcat-Access": "$env:MCPCAT_API_KEY"}
    for secret_or_upstream_value in (
        "upstream-secret.example.test",
        "upstream-token-123",
        "upstream-header-secret",
        "client-secret-value",
        "oauth-access-token",
        "private_upstream",
    ):
        assert secret_or_upstream_value not in package_text


async def test_request_base_url_is_used_when_public_base_url_is_not_configured(
    database: Database,
    runtime_patches: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        generator.ConfigService,
        "get_public_base_url",
        staticmethod(lambda: None),
    )

    result = await generator.generate_mcp_skill(
        database,
        manager=_manager(),
        server_name="Weather / China",
        actor="admin",
        fallback_base_url="http://localhost:8000/",
    )

    archive_path = tmp_path / "artifacts" / result.slug / "0.1.0.zip"
    with zipfile.ZipFile(archive_path) as archive:
        config = json.loads(
            archive.read(f"{result.slug}/config/mcporter.json").decode()
        )

    assert config["mcpServers"][result.slug]["url"] == (
        "${MCPCAT_URL:-http://localhost:8000}/mcp/Weather%20%2F%20China"
    )


async def test_configured_public_base_url_wins_over_request_base_url(
    database: Database,
    runtime_patches: None,
    tmp_path: Path,
) -> None:
    result = await generator.generate_mcp_skill(
        database,
        manager=_manager(),
        server_name="Weather / China",
        actor="admin",
        fallback_base_url="http://internal.example.test:8000",
    )

    archive_path = tmp_path / "artifacts" / result.slug / "0.1.0.zip"
    with zipfile.ZipFile(archive_path) as archive:
        config = json.loads(
            archive.read(f"{result.slug}/config/mcporter.json").decode()
        )

    assert config["mcpServers"][result.slug]["url"] == (
        "${MCPCAT_URL:-https://skills.example.test/mcpcat}" "/mcp/Weather%20%2F%20China"
    )


async def test_catalog_generates_only_search_and_call_contract(
    database: Database,
    runtime_patches: None,
    tmp_path: Path,
) -> None:
    manager = _catalog_manager()

    result = await generator.generate_catalog_skill(
        database,
        manager=manager,
        actor="admin",
    )

    archive_path = tmp_path / "artifacts" / result.slug / "0.1.0.zip"
    with zipfile.ZipFile(archive_path) as archive:
        config = json.loads(
            archive.read(f"{result.slug}/config/mcporter.json").decode()
        )
        skill_md = archive.read(f"{result.slug}/SKILL.md").decode()
        names = archive.namelist()

    server_config = config["mcpServers"][generator.CATALOG_SKILL_SLUG]
    assert result.slug == generator.CATALOG_SKILL_SLUG
    assert server_config["url"] == (
        "${MCPCAT_URL:-https://skills.example.test/mcpcat}/mcp/mcpcat"
    )
    assert server_config["allowedTools"] == ["call_tool", "search_tools"]
    metadata = _frontmatter(skill_md)
    assert metadata["description"] == generator.CATALOG_DESCRIPTION
    assert "references/service-instructions.md" not in "\n".join(names)
    assert skill_md.index("search_tools") < skill_md.index("call_tool")
    assert "A search result is not authorization" in skill_md
    assert generator.GENERATOR_VERSION == "1.0.2"
    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug(generator.CATALOG_SKILL_SLUG)
        assert skill.source_ref_json["catalog"] is True


async def test_catalog_membership_is_deduplicated_but_path_change_creates_draft(
    database: Database,
    runtime_patches: None,
) -> None:
    manager = _catalog_manager()
    first = await generator.generate_catalog_skill(
        database,
        manager=manager,
        actor="admin",
    )
    manager.catalog_service.membership_revision += 1
    unchanged = await generator.generate_catalog_skill(
        database,
        manager=manager,
        actor="admin",
    )
    manager.catalog_service._config.path_name = "tool-catalog"
    changed = await generator.generate_catalog_skill(
        database,
        manager=manager,
        actor="admin",
    )

    assert (first.changed, first.version) == (True, "0.1.0")
    assert (unchanged.changed, unchanged.version) == (False, "0.1.0")
    assert (changed.changed, changed.version) == (True, "0.1.1")


async def test_template_version_change_refreshes_description_in_new_draft(
    database: Database,
    runtime_patches: None,
) -> None:
    manager = _manager()
    first = await generator.generate_mcp_skill(
        database,
        manager=manager,
        server_name="Weather / China",
        actor="admin",
    )
    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug(first.slug)
        version = unit.skill_versions.get(skill.id, first.version)
        version.generator_version = "1.0.1"
        skill.description = "Use mcpcat through mcporter."
        unit.commit()

    refreshed = await generator.generate_mcp_skill(
        database,
        manager=manager,
        server_name="Weather / China",
        actor="admin",
    )

    assert (refreshed.changed, refreshed.version) == (True, "0.1.1")
    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug(first.slug)
        version = unit.skill_versions.get(skill.id, refreshed.version)
        assert "Use when" in skill.description
        assert "mcporter" not in skill.description.casefold()
        assert version.generator_version == generator.GENERATOR_VERSION
        assert (
            version.source_snapshot_json["generated_description"] == skill.description
        )


async def test_disabled_catalog_cannot_generate_skill(
    database: Database,
    runtime_patches: None,
) -> None:
    with pytest.raises(SkillDomainError, match="Catalog 未启用"):
        await generator.generate_catalog_skill(
            database,
            manager=_catalog_manager(enabled=False),
            actor="admin",
        )


async def test_base_url_change_creates_a_new_draft(
    database: Database,
    runtime_patches: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        generator.ConfigService,
        "get_public_base_url",
        staticmethod(lambda: None),
    )

    first = await generator.generate_mcp_skill(
        database,
        manager=_manager(),
        server_name="Weather / China",
        actor="admin",
        fallback_base_url="http://first.example.test",
    )
    unchanged = await generator.generate_mcp_skill(
        database,
        manager=_manager(),
        server_name="Weather / China",
        actor="admin",
        fallback_base_url="http://first.example.test/",
    )
    changed = await generator.generate_mcp_skill(
        database,
        manager=_manager(),
        server_name="Weather / China",
        actor="admin",
        fallback_base_url="https://second.example.test",
    )

    assert (first.changed, first.version) == (True, "0.1.0")
    assert (unchanged.changed, unchanged.version) == (False, "0.1.0")
    assert (changed.changed, changed.version) == (True, "0.1.1")


async def test_first_generation_deduplicates_then_creates_patch_draft_on_schema_change(
    database: Database,
    runtime_patches: None,
) -> None:
    manager = _manager()
    first = await generator.generate_mcp_skill(
        database,
        manager=manager,
        server_name="Weather / China",
        actor="admin",
    )
    unchanged = await generator.generate_mcp_skill(
        database,
        manager=manager,
        server_name="Weather / China",
        actor="admin",
    )
    manager.server_info["Weather / China"]["mcp"].tools.append(FakeTool("alerts"))
    changed = await generator.generate_mcp_skill(
        database,
        manager=manager,
        server_name="Weather / China",
        actor="admin",
    )

    assert (first.changed, first.version) == (True, "0.1.0")
    assert (unchanged.changed, unchanged.version, unchanged.version_id) == (
        False,
        "0.1.0",
        first.version_id,
    )
    assert (changed.changed, changed.version) == (True, "0.1.1")
    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug(first.slug)
        versions = unit.skill_versions.list_for_skill(skill.id)
        assert [item.version for item in versions] == ["0.1.1", "0.1.0"]
        assert all(item.status == "draft" for item in versions)
        assert versions[0].tool_schema_hash != versions[1].tool_schema_hash


async def test_unavailable_source_never_creates_empty_skill(
    database: Database,
    runtime_patches: None,
) -> None:
    with pytest.raises(SkillDomainError, match="未运行"):
        await generator.generate_mcp_skill(
            database,
            manager=_manager(status="stopped"),
            server_name="Weather / China",
            actor="admin",
        )

    with UnitOfWork(database) as unit:
        assert unit.skills.get_by_slug("weather-china") is None


async def test_tool_count_limit_blocks_oversized_generated_skill(
    runtime_patches: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generator.settings, "mcpcat_mcp_skill_max_tools", 2)
    manager = _manager(tools=[FakeTool("one"), FakeTool("two"), FakeTool("three")])

    with pytest.raises(SkillDomainError, match="工具数量超过"):
        await generator.snapshot_mcp_server(manager, "Weather / China")
