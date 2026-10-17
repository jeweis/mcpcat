"""固定版本 URI、Registry 授权和真实 ASGI 官方扩展/Resources 回归。"""

import base64
import hashlib
from dataclasses import replace
from uuid import uuid4

import httpx2
import pytest
from fastmcp import FastMCP
from test_skill_content_workflow import content_app

from app.services.remote_skill_registry import (
    RemoteSkillAccessError,
    RemoteSkillRegistry,
    SkillEndpointScope,
)
from app.services.skill_listing_cursors import SkillListingCursors
from app.services.skill_package_validator import build_deterministic_skill_zip
from app.services.skill_profile_upload import upload_skill_profiles
from app.services.skill_resource_uri import (
    SkillResourceIdentity,
    parse_skill_resource_uri,
    skill_resource_uri,
)
from app.services.skill_variant_publication import (
    publish_content_version,
    withdraw_remote_variant,
)
from app.services.skills_mcp_extension import (
    SKILLS_EXTENSION,
    RegistrySkillResources,
    RegistrySkillsExtension,
)


def skill_package(slug="demo", body=b"# Original\r\n", extras=None):
    return build_deterministic_skill_zip(
        {
            f"{slug}/SKILL.md": (
                f"---\r\nname: {slug}\r\ndescription: Demo tasks.\r\nlicense: MIT\r\n"
                "metadata:\r\n  owner: fixture\r\nx-extra: [one, two]\r\n---\r\n"
            ).encode()
            + body,
            **{f"{slug}/{path}": data for path, data in (extras or {}).items()},
        }
    )


@pytest.fixture
def registry_setup(content_app):
    _, database = content_app
    scope = SkillEndpointScope(str(uuid4()), str(uuid4()))
    state = {"authenticated": True, "available": True, "principal": "reader-a"}

    def registry(current_scope=scope):
        return RemoteSkillRegistry(
            database,
            current_scope,
            authorize=lambda: state["authenticated"],
            available=lambda: state["available"],
        )

    def publish(slug="demo", version="1.0.0", **kwargs):
        upload_skill_profiles(
            database,
            content=skill_package(slug, **kwargs),
            version=version,
            selection="remote",
        )
        publish_content_version(database, slug=slug, version=version)

    return database, scope, state, registry, publish


def test_uri_roundtrip_safe_encoding_and_origin():
    identity = SkillResourceIdentity(
        str(uuid4()), str(uuid4()), 3, "1.0.0+build.1", "demo", "references/中文 %.md"
    )
    uri = skill_resource_uri(identity)
    assert parse_skill_resource_uri(uri) == identity
    assert "+" not in uri and "%2B" in uri
    for invalid in (
        uri + "?key=secret",
        uri + "#fragment",
        uri.replace("mcpcat/", "evil/"),
        uri.replace("/remote/", "/local/"),
        uri.replace("/demo/", "/../"),
        uri.replace("/references/", "/%2e%2e/"),
        uri.replace("/references/", "/refs%2F../"),
        uri.replace("/references/", "/refs//"),
        uri.replace("/references/", "/%00/"),
    ):
        with pytest.raises(ValueError):
            parse_skill_resource_uri(invalid)


def test_registry_direct_get_raw_manifest_and_binary(registry_setup):
    _, _, _, registry, publish = registry_setup
    publish(
        extras={
            "assets/file.bin": b"\x00\xff\x80",
            "references/large.md": b"a" * 600_000,
        }
    )
    entry = registry().list_skills()[0]
    assert registry().get_skill(entry["uri"]) == entry
    assert entry["frontmatter"]["x-extra"] == ["one", "two"]
    assert entry["frontmatter"]["license"] == "MIT"
    for file in entry["resources"]:
        data, _ = registry().read_file(file["uri"])
        assert len(data) == file["size"]
        assert "sha256:" + hashlib.sha256(data).hexdigest() == file["digest"]
    markdown, media = registry().read_file(entry["uri"])
    assert b"\r\n" in markdown and media == "text/markdown"
    with pytest.raises(RemoteSkillAccessError):
        registry().get_skill(
            next(
                file["uri"]
                for file in entry["resources"]
                if file["uri"] != entry["uri"]
            )
        )


def test_fixed_uri_survives_new_latest_but_not_withdrawal(registry_setup):
    database, _, _, registry, publish = registry_setup
    publish()
    old = registry().list_skills()[0]
    original, _ = registry().read_file(old["uri"])
    publish(version="1.0.1", body=b"# New\n")
    assert registry().list_skills()[0]["uri"] != old["uri"]
    assert registry().get_skill(old["uri"]) == old
    assert registry().read_file(old["uri"])[0] == original
    withdraw_remote_variant(database, slug="demo", version="1.0.0")
    with pytest.raises(RemoteSkillAccessError):
        registry().get_skill(old["uri"])


def test_scope_identity_drafts_and_live_authorization(registry_setup):
    database, scope, state, registry, publish = registry_setup
    publish()
    entry = registry().list_skills()[0]
    other = replace(scope, endpoint=str(uuid4()))
    with pytest.raises(RemoteSkillAccessError):
        registry(other).read_file(entry["uri"])
    identity = parse_skill_resource_uri(entry["uri"])
    with pytest.raises(RemoteSkillAccessError):
        registry().get_skill(
            skill_resource_uri(replace(identity, skill_id=identity.skill_id + 1))
        )
    upload_skill_profiles(
        database,
        content=skill_package(body=b"# Draft\n"),
        version="1.0.1",
        selection="remote",
    )
    with pytest.raises(RemoteSkillAccessError):
        registry().get_skill(skill_resource_uri(replace(identity, version="1.0.1")))
    for field in ("authenticated", "available"):
        state[field] = False
        with pytest.raises(RemoteSkillAccessError):
            registry().read_file(entry["uri"])
        with pytest.raises(RemoteSkillAccessError):
            registry().list_skills()
        state[field] = True


def test_anonymous_requires_endpoint_and_explicit_review(registry_setup):
    database, scope, state, registry, publish = registry_setup
    publish()
    private = registry().list_skills()[0]["uri"]
    state["authenticated"] = False
    public_scope = replace(scope, require_auth=False)
    assert registry(public_scope).list_skills() == []
    with pytest.raises(RemoteSkillAccessError):
        registry(public_scope).read_file(private)
    upload_skill_profiles(
        database, content=skill_package(), version="1.0.1", selection="remote"
    )
    publish_content_version(database, slug="demo", version="1.0.1", remote_public=True)
    assert len(registry(public_scope).list_skills()) == 1
    with pytest.raises(RemoteSkillAccessError):
        registry().list_skills()


def test_service_binding_cannot_leak_to_independent_or_other_service(registry_setup):
    database, scope, _, registry, _ = registry_setup
    service_scope = replace(
        scope, kind="service", binding=scope.endpoint, source_name="source-a"
    )
    upload_skill_profiles(
        database,
        content=skill_package(),
        version="1.0.0",
        selection="remote",
        binding=scope.endpoint,
        metadata={"binding_kind": "service", "source_name": "source-a"},
    )
    publish_content_version(database, slug="demo", version="1.0.0")
    entry = registry(service_scope).list_skills()[0]
    assert registry().list_skills() == []
    assert registry(replace(service_scope, source_name="source-b")).list_skills() == []
    with pytest.raises(RemoteSkillAccessError):
        registry().read_file(entry["uri"])


def test_cursor_bounded_expiring_and_cross_owner():
    cursors = SkillListingCursors(capacity=1)
    first = cursors.save(("uri-a", "uri-b"), 1, "identity-a")
    assert cursors.get(first, "identity-a").position == 1
    with pytest.raises(ValueError):
        cursors.get(first, "identity-b")
    second = cursors.save(("uri-a", "uri-b"), 1, "identity-a")
    with pytest.raises(ValueError):
        cursors.get(first, "identity-a")
    assert cursors.get(second, "identity-a").uris == ("uri-a", "uri-b")
    expired = SkillListingCursors(ttl_seconds=0)
    token = expired.save(("uri-a",), 0, "identity-a")
    with pytest.raises(ValueError):
        expired.get(token, "identity-a")


@pytest.mark.asyncio
async def test_owned_skill_resource_metadata_uses_extension_namespace(registry_setup):
    _, _, _, registry, publish = registry_setup
    publish()
    provider = RegistrySkillResources(registry)
    resources = await provider.list_resources()
    metadata = resources[0].to_mcp_resource().meta
    assert "fastmcp" not in metadata
    assert metadata["io.modelcontextprotocol.skills/fastmcp"] == {"tags": []}


@pytest.mark.parametrize("boundary", ["files", "bytes"])
def test_remote_standard_limit_is_inclusive(registry_setup, boundary):
    _, _, _, registry, publish = registry_setup
    if boundary == "files":
        extras = {f"references/file-{index}.txt": b"x" for index in range(511)}
    else:
        instruction_size = len(registry_instruction_bytes())
        total = 16 * 1024 * 1024 - instruction_size
        extras = {
            "assets/a.bin": b"a" * (total // 2),
            "assets/b.bin": b"b" * (total - total // 2),
        }
    publish(extras=extras)
    entry = registry().list_skills()[0]
    if boundary == "files":
        assert len(entry["resources"]) == 512
    else:
        assert sum(file["size"] for file in entry["resources"]) == 16 * 1024 * 1024
        largest = max(entry["resources"], key=lambda file: file["size"])
        assert len(registry().read_file(largest["uri"])[0]) == largest["size"]


def registry_instruction_bytes():
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(skill_package())) as archive:
        return archive.read("demo/SKILL.md")


async def request(client, method, params=None, *, opt_in=True):
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "fixture", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {
            "extensions": {SKILLS_EXTENSION: {}} if opt_in else {},
        },
    }
    headers = {
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": method,
        "Accept": "application/json, text/event-stream",
    }
    if params and "uri" in params:
        headers["Mcp-Name"] = params["uri"]
    response = await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": {**(params or {}), "_meta": meta},
        },
    )
    payload = response.json()
    if response.status_code != 200:
        assert (
            response.status_code == {-32602: 400, -32601: 404}[payload["error"]["code"]]
        )
    return payload


@pytest.mark.asyncio
async def test_actual_modern_extension_pagination_resources_and_errors(registry_setup):
    database, _, state, registry, publish = registry_setup
    for slug in ("alpha", "beta", "gamma"):
        publish(slug=slug, extras={"assets/blob.bin": b"\xff\x80"})
    server = FastMCP("Registry skills fixture")
    server.add_extension(
        RegistrySkillsExtension(
            registry, principal=lambda: state["principal"], page_size=1
        )
    )
    server.add_provider(RegistrySkillResources(registry))
    app = server.http_app(path="/mcp", json_response=True)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            discovery = (await request(client, "server/discover"))["result"]
            assert discovery["capabilities"]["extensions"][SKILLS_EXTENSION] == {
                "directoryRead": True
            }
            assert "resources" in discovery["capabilities"]
            first = (await request(client, "skills/list"))["result"]
            assert (
                first["resultType"] == "complete"
                and first["cacheScope"] == "private"
                and first["ttlMs"] == 0
            )
            assert (
                len(first["skills"]) == 1 and len(first["skills"][0]["resources"]) == 2
            )
            cursor = first["nextCursor"]
            state["principal"] = "reader-b"
            assert (await request(client, "skills/list", {"cursor": cursor}))["error"][
                "code"
            ] == -32602
            state["principal"] = "reader-a"
            publish(slug="beta", version="1.0.1", body=b"# New Beta\n")
            second = (await request(client, "skills/list", {"cursor": cursor}))[
                "result"
            ]
            retry = (await request(client, "skills/list", {"cursor": cursor}))["result"]
            assert second["skills"] == retry["skills"]
            third = (
                await request(client, "skills/list", {"cursor": second["nextCursor"]})
            )["result"]
            entries = first["skills"] + second["skills"] + third["skills"]
            assert {entry["frontmatter"]["name"] for entry in entries} == {
                "alpha",
                "beta",
                "gamma",
            }
            assert all("/1.0.0/" in entry["uri"] for entry in entries)
            assert "nextCursor" not in third
            entry = entries[0]
            assert (await request(client, "skills/get", {"uri": entry["uri"]}))[
                "result"
            ]["skill"] == entry
            for file in entry["resources"]:
                result = (
                    await request(client, "resources/read", {"uri": file["uri"]})
                )["result"]
                item = result["contents"][0]
                data = (
                    item["text"].encode()
                    if "text" in item
                    else base64.b64decode(item["blob"])
                )
                assert (
                    len(data) == file["size"]
                    and "sha256:" + hashlib.sha256(data).hexdigest() == file["digest"]
                )
                assert result["cacheScope"] == "private" and result["ttlMs"] == 0
            assert (await request(client, "skills/list", opt_in=False))["error"][
                "code"
            ] == -32601
            assert (
                await request(client, "skills/get", {"uri": "skill://other/SKILL.md"})
            )["error"]["code"] == -32602
            withdraw_remote_variant(
                database, slug=entry["frontmatter"]["name"], version="1.0.0"
            )
            assert (await request(client, "resources/read", {"uri": entry["uri"]}))[
                "error"
            ]["code"] == -32602


@pytest.mark.asyncio
async def test_legacy_omits_extension_but_preserves_tools_and_ordinary_resources(
    registry_setup,
):
    _, _, state, registry, publish = registry_setup
    publish()
    server = FastMCP("Legacy Registry fixture")
    server.add_extension(
        RegistrySkillsExtension(registry, principal=lambda: state["principal"])
    )
    server.add_provider(RegistrySkillResources(registry))

    @server.tool()
    def echo(message: str) -> str:
        return message

    app = server.http_app(path="/mcp", json_response=True)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            headers = {"Accept": "application/json, text/event-stream"}
            initialized = await client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "legacy-fixture", "version": "1"},
                    },
                },
            )
            assert initialized.status_code == 200
            assert "extensions" not in initialized.json()["result"]["capabilities"]
            headers.update(
                {
                    "MCP-Protocol-Version": "2025-11-25",
                    "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"],
                }
            )
            notified = await client.post(
                "/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
            assert notified.status_code == 202
            for index, (method, params) in enumerate(
                (
                    ("tools/list", {}),
                    ("resources/read", {"uri": registry().list_skills()[0]["uri"]}),
                    ("skills/list", {}),
                ),
                2,
            ):
                response = await client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": index,
                        "method": method,
                        "params": params,
                    },
                )
                payload = response.json()
                if method == "skills/list":
                    assert payload["error"]["code"] == -32601
                elif method == "tools/list":
                    assert [tool["name"] for tool in payload["result"]["tools"]] == [
                        "echo"
                    ]
                else:
                    assert payload["result"]["contents"][0]["text"].endswith(
                        "# Original\r\n"
                    )
