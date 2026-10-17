"""模式批次、同快照、历史字节/独立上下文及生产 API 回归。"""

import asyncio

import httpx2
import pytest
from test_mcp_skill_generator import (
    _catalog_manager,
    _manager,
    database,
    runtime_patches,
)
from test_skill_content_workflow import READ, WRITE, content_app
from test_skills_mcp_gateway import rpc
from test_skills_source_gateway import source_app

from app.services import mcp_skill_generator as generator
from app.services import skill_generation_batch as batch
from app.services.skill_content_service import latest_variant, load_variant
from app.services.skill_variant_publication import publish_content_version
from app.storage.runtime_identities import endpoint_identity
from app.storage.unit_of_work import UnitOfWork


async def generate(database, manager, selection="both", **kwargs):
    return await generator.generate_mcp_skill(
        database,
        manager=manager,
        server_name="Weather / China",
        actor="fixture",
        selection=selection,
        **kwargs,
    )


async def test_both_variants_share_snapshot_and_batch_publication(
    database, runtime_patches
):
    manager = _manager()
    result = await generate(database, manager)
    binding = endpoint_identity(database, "service", "Weather / China")
    with UnitOfWork(database) as unit:
        version = unit.skill_versions.get_by_id(result.version_id)
        assert version.status == "draft"
        assert version.source_snapshot_json["content_profiles"] == ["local", "remote"]
        rows = unit.skill_variants.list_for_version(result.version_id)
        assert {row.metadata_json["tool_schema_hash"] for row in rows} == {
            result.tool_schema_hash
        }
        assert all(row.metadata_json["required_tools"] == ["forecast"] for row in rows)
    local, local_bytes = load_variant(
        database,
        slug=result.slug,
        version=result.version,
        profile="local",
        include_drafts=True,
    )
    remote, remote_bytes = load_variant(
        database,
        slug=result.slug,
        version=result.version,
        profile="remote",
        binding=binding,
        include_drafts=True,
    )
    assert local["frontmatter"]["description"] == remote["frontmatter"]["description"]
    assert local_bytes != remote_bytes
    assert (
        latest_variant(database, slug=result.slug, profile="remote", binding=binding)
        is None
    )
    publish_content_version(database, slug=result.slug, version=result.version)
    assert latest_variant(database, slug=result.slug, profile="local") == result.version
    assert (
        latest_variant(database, slug=result.slug, profile="remote", binding=binding)
        == result.version
    )
    again = await generate(database, manager)
    assert not again.changed and again.version_id == result.version_id


async def test_remote_only_does_not_depend_on_address_or_move_local_latest(
    database, runtime_patches, monkeypatch
):
    manager = _manager()
    local = await generate(database, manager, "local")
    publish_content_version(database, slug=local.slug, version=local.version)
    before, original = load_variant(
        database, slug=local.slug, version=local.version, profile="local"
    )
    monkeypatch.setattr(
        generator.ConfigService, "get_public_base_url", staticmethod(lambda: None)
    )
    remote = await generate(database, manager, "remote")
    publish_content_version(database, slug=remote.slug, version=remote.version)
    same = await generate(
        database, manager, "remote", fallback_base_url="https://unrelated.test"
    )
    assert not same.changed and same.version_id == remote.version_id
    assert latest_variant(database, slug=local.slug, profile="local") == local.version
    after, content = load_variant(
        database, slug=local.slug, version=local.version, profile="local"
    )
    assert before["sha256"] == after["sha256"] and content == original


async def test_address_change_reuses_frozen_remote_bytes(
    database, runtime_patches, monkeypatch
):
    manager = _manager()
    first = await generate(database, manager)
    publish_content_version(database, slug=first.slug, version=first.version)
    binding = endpoint_identity(database, "service", "Weather / China")
    before, remote = load_variant(
        database,
        slug=first.slug,
        version=first.version,
        profile="remote",
        binding=binding,
    )
    monkeypatch.setattr(
        generator.ConfigService,
        "get_public_base_url",
        staticmethod(lambda: "https://new-address.test"),
    )
    second = await generate(database, manager)
    assert second.changed
    after, content = load_variant(
        database,
        slug=second.slug,
        version=second.version,
        profile="remote",
        binding=binding,
        include_drafts=True,
    )
    assert content == remote and after["sha256"] == before["sha256"]
    with UnitOfWork(database) as unit:
        assert (
            unit.skill_variants.get(
                first.version_id, "remote", binding
            ).remote_artifact_id
            == unit.skill_variants.get(
                second.version_id, "remote", binding
            ).remote_artifact_id
        )


async def test_duplicate_concurrent_generations_create_one_batch(
    database, runtime_patches
):
    manager = _manager()
    results = await asyncio.gather(
        generate(database, manager), generate(database, manager)
    )
    assert len({result.version_id for result in results}) == 1
    assert sorted(result.changed for result in results) == [False, True]


async def test_partial_failure_leaves_history_and_no_success_audit(
    database, runtime_patches, monkeypatch
):
    manager = _manager()
    original = await generate(database, manager, "local")
    publish_content_version(database, slug=original.slug, version=original.version)
    _, before = load_variant(
        database, slug=original.slug, version=original.version, profile="local"
    )

    def fail(*args, **kwargs):
        raise RuntimeError("fixture-sensitive-text")

    monkeypatch.setattr(batch, "store_remote_variant", fail)
    with pytest.raises(RuntimeError):
        await generate(database, manager)
    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug(original.slug)
        assert len(unit.skill_versions.list_for_skill(skill.id)) == 1
        events = unit.skill_audit.list_for_skill(skill.slug)
        assert events[-1].outcome == "failed"
        assert events[-1].details_json == {"error_type": "RuntimeError"}
    assert (
        load_variant(
            database, slug=original.slug, version=original.version, profile="local"
        )[1]
        == before
    )


async def test_catalog_remote_has_only_synthetic_tool_binding(
    database, runtime_patches, monkeypatch
):
    monkeypatch.setattr(
        generator.ConfigService, "get_public_base_url", staticmethod(lambda: None)
    )
    result = await generator.generate_catalog_skill(
        database, manager=_catalog_manager(), actor="fixture", selection="remote"
    )
    binding = endpoint_identity(database, "catalog")
    info, _ = load_variant(
        database,
        slug=result.slug,
        version=result.version,
        profile="remote",
        binding=binding,
        include_drafts=True,
    )
    assert set(info["metadata"]["required_tools"]) == {"search_tools", "call_tool"}
    assert info["metadata"]["binding_kind"] == "catalog"


async def test_source_replaced_during_snapshot_does_not_bind_old_content_to_new_source(
    database, runtime_patches, monkeypatch
):
    manager = _manager()
    original_provider = generator.FastMCPProvider

    class ReplacingProvider(original_provider):
        async def list_tools(self):
            tools = await super().list_tools()
            manager.server_info["Weather / China"]["mcp"] = _manager().server_info[
                "Weather / China"
            ]["mcp"]
            return tools

    monkeypatch.setattr(generator, "FastMCPProvider", ReplacingProvider)
    with pytest.raises(ValueError, match="来源已变更"):
        await generate(database, manager)
    with UnitOfWork(database) as unit:
        assert unit.skills.get_by_slug("weather-china") is None


@pytest.mark.asyncio
async def test_mode_api_published_remote_is_readable_on_real_source(
    content_app, monkeypatch
):
    _, database = content_app
    app = source_app(database, monkeypatch)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            path = "/api/skill-variants/generate/service/alpha"
            assert (
                await client.post(
                    path, headers={"Mcpcat-Key": READ}, json={"selection": "both"}
                )
            ).status_code == 403
            response = await client.post(
                path, headers={"Mcpcat-Key": WRITE}, json={"selection": "both"}
            )
            assert response.status_code == 200
            result = response.json()
            assert result["selection"] == "both" and result["status"] == "draft"
            assert (await rpc(client, path="alpha")).json()["result"]["skills"] == []
            publish = await client.post(
                f'/api/skill-variants/{result["slug"]}/versions/{result["version"]}/publish',
                headers={"Mcpcat-Key": WRITE},
            )
            assert publish.status_code == 200
            entry = (await rpc(client, path="alpha")).json()["result"]["skills"][0]
            assert entry["frontmatter"]["metadata"]["mcpcat-profile"] == "remote"
            read = await rpc(
                client, "resources/read", {"uri": entry["uri"]}, path="alpha"
            )
            assert (
                "Host's MCP tool interface"
                in read.json()["result"]["contents"][0]["text"]
            )
            assert (
                await client.get(
                    f'/api/skills/{result["slug"]}/versions/{result["version"]}/download',
                    headers={"Mcpcat-Key": READ},
                )
            ).status_code == 200
