"""正式 MCP 边界的恶意 URI、摘要漂移与真实并发发布读取。"""

import asyncio
import threading
from copy import deepcopy
from dataclasses import replace

import httpx2
import pytest
from test_remote_skill_protocol import skill_package
from test_skill_content_workflow import content_app
from test_skills_mcp_gateway import configured_app, publish, rpc

from app.services.skill_profile_upload import upload_skill_profiles
from app.services.skill_resource_uri import parse_skill_resource_uri, skill_resource_uri
from app.services.skill_variant_publication import publish_content_version
from app.storage.unit_of_work import UnitOfWork


def invalid_uris(entry: dict) -> list[str]:
    """目录穿越、其他来源/模式、非清单文件均不能扩大读取范围。"""
    uri = entry["uri"]
    identity = parse_skill_resource_uri(uri)
    return [
        uri + "?token=not-real",
        uri + "#fragment",
        uri.replace("/remote/", "/local/"),
        uri.replace("mcpcat/", "other/"),
        uri.replace("/demo/", "/../"),
        uri.replace("/SKILL.md", "/%2e%2e/config.json"),
        uri.replace("/SKILL.md", "/%252e%252e/config.json"),
        uri.replace("/SKILL.md", "/missing.txt"),
        uri.replace("/SKILL.md", "/a%2fb.txt"),
        skill_resource_uri(
            replace(identity, endpoint="00000000-0000-0000-0000-000000000001")
        ),
        "file:///etc/passwd",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["skills/get", "resources/read"])
async def test_malicious_uris_fail_at_real_protocol_boundary(
    content_app, monkeypatch, method
):
    _, database = content_app
    app = configured_app(database, monkeypatch)
    publish(database)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            entry = (await rpc(client)).json()["result"]["skills"][0]
            for uri in invalid_uris(entry):
                response = await rpc(client, method, {"uri": uri})
                assert response.json()["error"]["code"] == -32602
                assert "private, no-store" == response.headers["cache-control"]
                assert "result" not in response.json()
                assert "# Original" not in response.text


async def read_snapshot(client, entry):
    response = await rpc(client, "skills/get", {"uri": entry["uri"]})
    assert response.json()["result"]["skill"] == entry
    content = (await rpc(client, "resources/read", {"uri": entry["uri"]})).json()[
        "result"
    ]["contents"][0]["text"]
    assert content.endswith("# Original\r\n")


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["frontmatter", "digest"])
async def test_manifest_drift_is_rejected_even_after_successful_discovery(
    content_app, monkeypatch, damage
):
    _, database = content_app
    app = configured_app(database, monkeypatch)
    publish(database)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            entry = (await rpc(client)).json()["result"]["skills"][0]
            with UnitOfWork(database) as unit:
                version = unit.skill_versions.get(
                    unit.skills.get_by_slug("demo").id, "1.0.0"
                )
                variant = unit.skill_variants.get(version.id, "remote")
                if damage == "frontmatter":
                    variant.frontmatter_json = {
                        **variant.frontmatter_json,
                        "description": "changed after approval",
                    }
                else:
                    manifest = deepcopy(variant.manifest_json)
                    manifest["files"][0]["sha256"] = "0" * 64
                    variant.manifest_json = manifest
                unit.commit()
            for method in ("skills/get", "resources/read"):
                response = await rpc(client, method, {"uri": entry["uri"]})
                assert response.json()["error"]["code"] == -32602
                assert response.headers["cache-control"] == "private, no-store"
            assert (await rpc(client)).json()["result"]["skills"] == []


@pytest.mark.asyncio
async def test_publish_in_another_thread_never_exposes_half_manifest(
    content_app, monkeypatch
):
    _, database = content_app
    app = configured_app(database, monkeypatch)
    publish(database)
    upload_skill_profiles(
        database,
        content=skill_package(body=b"# New immutable version\n"),
        version="1.0.1",
        selection="remote",
    )
    before_commit, release = threading.Event(), threading.Event()
    original = UnitOfWork.commit

    def paused_commit(unit):
        before_commit.set()
        assert release.wait(5)
        return original(unit)

    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            old = (await rpc(client)).json()["result"]["skills"][0]
            monkeypatch.setattr(UnitOfWork, "commit", paused_commit)
            writer = asyncio.create_task(
                asyncio.to_thread(
                    publish_content_version, database, slug="demo", version="1.0.1"
                )
            )
            try:
                assert await asyncio.to_thread(before_commit.wait, 5)
                assert not writer.done()
                assert (await rpc(client)).json()["result"]["skills"] == [old]
                await read_snapshot(client, old)
            finally:
                release.set()
                await asyncio.wait_for(writer, 5)
            latest = (await rpc(client)).json()["result"]["skills"][0]
            assert latest["uri"] != old["uri"] and "/1.0.1/" in latest["uri"]
            await read_snapshot(client, old)
