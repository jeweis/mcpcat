"""官方目录读取：固定清单、直接子项、分页和每次访问授权。"""

from uuid import uuid4

import httpx2
import pytest
from fastmcp import Client, FastMCP
from mcp.client.extension import advertise
from test_remote_skill_protocol import registry_setup, request
from test_skill_content_workflow import content_app

from app.services import inspector_skills
from app.services.remote_skill_registry import RemoteSkillAccessError
from app.services.skill_variant_publication import withdraw_remote_variant
from app.services.skills_mcp_extension import (
    RegistrySkillResources,
    RegistrySkillsExtension,
)


def test_directory_uses_frozen_manifest_and_direct_children(registry_setup):
    database, scope, state, registry, publish = registry_setup
    publish(extras={"references/中文 %.md": b"hello", "references/deep/a.bin": b"\xff"})
    root = registry().list_skills()[0]["uri"].removesuffix("/SKILL.md")
    assert registry().read_directory(root) == [
        {"uri": root + "/SKILL.md", "name": "SKILL.md", "mimeType": "text/markdown"},
        {
            "uri": root + "/references",
            "name": "references",
            "mimeType": "inode/directory",
        },
    ]
    children = registry().read_directory(root + "/references")
    assert {entry["name"] for entry in children} == {"中文 %.md", "deep"}
    assert len(registry().read_directory(root + "/references/deep")) == 1
    for suffix in (
        "/",
        "/missing",
        "/SKILL.md",
        "/../",
        "/%2e%2e",
        "?q=x",
        "#fragment",
    ):
        with pytest.raises(RemoteSkillAccessError):
            registry().read_directory(root + suffix)
    for original in (scope.instance, scope.endpoint):
        with pytest.raises(RemoteSkillAccessError):
            registry().read_directory(root.replace(original, str(uuid4())))
    publish(version="1.0.1", extras={"new.md": b"new"})
    assert registry().read_directory(root + "/references") == children
    state["authenticated"] = False
    with pytest.raises(RemoteSkillAccessError):
        registry().read_directory(root)
    state["authenticated"] = True
    withdraw_remote_variant(database, slug="demo", version="1.0.0")
    with pytest.raises(RemoteSkillAccessError):
        registry().read_directory(root)


@pytest.mark.asyncio
async def test_directory_protocol_paging_optin_and_revocation(registry_setup):
    database, _, state, registry, publish = registry_setup
    publish(extras={"references/a.md": b"a", "references/b.md": b"b"})
    root = registry().list_skills()[0]["uri"].removesuffix("/SKILL.md")
    server = FastMCP("directory fixture")
    server.add_extension(
        RegistrySkillsExtension(
            registry, principal=lambda: state["principal"], page_size=1
        )
    )
    server.add_provider(RegistrySkillResources(registry))
    app = server.http_app(path="/mcp", json_response=True)
    async with app.router.lifespan_context(app), httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        method = "resources/directory/read"
        first = (await request(client, method, {"uri": root}))["result"]
        assert first["resultType"] == "complete" and first["cacheScope"] == "private"
        cursor = first["nextCursor"]
        params = {"uri": root, "cursor": cursor}
        second = (await request(client, method, params))["result"]
        assert [r["name"] for r in first["resources"] + second["resources"]] == [
            "SKILL.md",
            "references",
        ]
        assert "nextCursor" not in second
        assert (await request(client, method, params))["result"] == second
        assert (await request(client, method, {"uri": root}, opt_in=False))["error"][
            "code"
        ] == -32601
        state["principal"] = "other"
        assert (await request(client, method, params))["error"]["code"] == -32602
        state["principal"] = "reader-a"
        assert (await request(client, method, {**params, "uri": root + "/references"}))[
            "error"
        ]["code"] == -32602
        withdraw_remote_variant(database, slug="demo", version="1.0.0")
        assert (await request(client, method, params))["error"]["code"] == -32602


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_inspector_directory_through_real_client(registry_setup, mode):
    _, _, state, registry, publish = registry_setup
    publish(extras={"references/a.md": b"reference"})
    server = FastMCP("Inspector directory fixture")
    server.add_extension(
        RegistrySkillsExtension(registry, principal=lambda: state["principal"])
    )
    server.add_provider(RegistrySkillResources(registry))
    uri = registry().list_skills()[0]["uri"]
    root = uri.removesuffix("/SKILL.md")
    async with Client(
        server, mode=mode, extensions=[advertise(inspector_skills.EXTENSION)]
    ) as client:
        if mode == "legacy":
            with pytest.raises(ValueError, match="未声明"):
                await inspector_skills.read_directory(client, uri, root)
            assert (await client.read_resource(uri))[0].text.startswith("---")
        else:
            held = await inspector_skills.get_skill(client, uri, "official")
            result = await inspector_skills.read_directory(client, uri, root)
            assert [item["name"] for item in result["resources"]] == [
                "SKILL.md",
                "references",
            ]
            reference = await inspector_skills.read_directory(
                client, uri, root + "/references"
            )
            assert reference["resources"][0]["name"] == "a.md"
            assert await inspector_skills.get_skill(client, uri, "official") == held
