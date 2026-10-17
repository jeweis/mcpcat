"""Inspector 的协议边界、字节完整性和应用会话隔离。"""

import base64
from hashlib import sha256
from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP
from mcp.client.extension import advertise

from app.services import inspector_skills as skills
from app.services.inspector_service import InspectorService, InspectorSession

URI = "skill://demo/SKILL.md"
BODY = b"---\nname: demo\ndescription: Example\n---\nHello\n"


def entry():
    return {
        "uri": URI,
        "frontmatter": {"name": "demo", "description": "Example"},
        "resources": [
            {
                "uri": URI,
                "size": len(BODY),
                "digest": "sha256:" + sha256(BODY).hexdigest(),
            }
        ],
    }


class ClientFixture:
    async def read_resource(self, uri):
        return [SimpleNamespace(uri=uri, text=BODY.decode(), mime_type="text/markdown")]


@pytest.mark.asyncio
async def test_provider_is_explicit_and_legacy_compatible(tmp_path):
    from fastmcp.server.providers.skills import SkillProvider

    directory = tmp_path / "demo"
    directory.mkdir()
    (directory / "SKILL.md").write_bytes(BODY)
    server = FastMCP("legacy Provider")
    server.add_provider(SkillProvider(directory))
    async with Client(server, mode="legacy") as client:
        with pytest.raises(ValueError, match="未声明"):
            await skills.list_skills(client, "official", None)
        listed = await skills.list_skills(client, "provider", None)
        assert listed["skills"][0]["uri"] == URI
        result = await skills.read_file(client, URI, URI, "provider")
        assert result["verification"] == "provider"
        assert result["text"] == BODY.decode()


@pytest.mark.asyncio
async def test_directory_gate_does_not_send_unknown_method():
    server = FastMCP("No extension")
    async with Client(server) as client:
        with pytest.raises(ValueError, match="未声明"):
            await skills.read_directory(client, URI, "skill://demo")


@pytest.mark.asyncio
async def test_held_manifest_is_not_silently_refreshed(monkeypatch):
    async def forbidden(*args):
        raise AssertionError("must not silently refresh")

    monkeypatch.setattr(skills, "get_skill", forbidden)
    result = await skills.read_file(ClientFixture(), URI, URI, "official", entry())
    assert result["verification"] == "verified"


@pytest.mark.asyncio
async def test_read_verifies_raw_bytes_and_frontmatter(monkeypatch):
    async def get(*args):
        return {"skill": entry()}

    monkeypatch.setattr(skills, "get_skill", get)
    result = await skills.read_file(ClientFixture(), URI, URI, "official")
    assert result["verification"] == "verified"
    assert result["text"] == BODY.decode()
    bad = entry()
    bad["resources"][0]["size"] += 1

    async def changed(*args):
        return {"skill": bad}

    monkeypatch.setattr(skills, "get_skill", changed)
    with pytest.raises(ValueError, match="SHA256"):
        await skills.read_file(ClientFixture(), URI, URI, "official")
    bad["resources"][0]["size"] -= 1
    bad["frontmatter"]["description"] = "different"
    with pytest.raises(ValueError, match="frontmatter"):
        await skills.read_file(ClientFixture(), URI, URI, "official")


@pytest.mark.parametrize(
    "field,value", [("size", None), ("digest", None), ("uri", "skill://demo/../escape")]
)
def test_incomplete_manifest_rejected(field, value):
    item = entry()
    item["resources"][0][field] = value
    with pytest.raises(ValueError):
        skills.validate_entry(item)


def test_dynamic_and_session_identity():
    item = entry()
    item["resources"] = "dynamic"
    assert skills.validate_entry(item) == item
    service = InspectorService()
    session = InspectorSession("demo", ClientFixture(), "owner")
    service.sessions[session.id] = session
    assert service.authorize(session.id, "owner") is session
    with pytest.raises(PermissionError):
        service.authorize(session.id, "other")


@pytest.mark.asyncio
async def test_binary_raw_bytes():
    class BinaryClient:
        async def read_resource(self, uri):
            return [
                SimpleNamespace(uri=uri, blob=base64.b64encode(b"\x00\xff").decode())
            ]

    raw, _ = await skills.read_bytes(BinaryClient(), URI)
    assert raw == b"\x00\xff"


@pytest.mark.asyncio
async def test_real_sdk_custom_request_metadata_and_pure_skills():
    from fastmcp.server.extensions import MethodBinding, ServerExtension

    from app.services.skills_mcp_extension import GetSkillParams, ListSkillsParams

    class Extension(ServerExtension):
        identifier = skills.EXTENSION

        def methods(self):
            return [
                MethodBinding("skills/list", ListSkillsParams, self.list),
                MethodBinding("skills/get", GetSkillParams, self.get),
            ]

        async def list(self, context, params):
            assert self.client_settings(context) is not None
            return {
                "skills": [] if params.cursor else [entry()],
                **({} if params.cursor else {"nextCursor": "page-2"}),
                "resultType": "complete",
                "ttlMs": 0,
                "cacheScope": "private",
            }

        async def get(self, context, params):
            assert params.uri == URI
            return {
                "skill": entry(),
                "resultType": "complete",
                "ttlMs": 0,
                "cacheScope": "private",
            }

    server = FastMCP("Inspector")
    server.add_extension(Extension())

    @server.resource(URI)
    def document():
        return BODY.decode()

    client = Client(server, extensions=[advertise(skills.EXTENSION)])
    async with client:
        listed = await skills.list_skills(client, "official", None)
        assert listed["skills"][0]["uri"] == URI
        assert listed["nextCursor"] == "page-2"
        assert (await skills.list_skills(client, "official", "page-2"))["skills"] == []
        assert (await skills.read_file(client, URI, URI, "official"))[
            "verification"
        ] == "verified"
