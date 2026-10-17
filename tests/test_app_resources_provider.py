"""原生 MCP Client 实际读取固定 UI/附件，撤销/身份检查持续生效。"""

import base64
import io
import json
import zipfile
from dataclasses import replace

import pytest
from fastmcp import Client, FastMCP
from fastmcp.apps import UI_MIME_TYPE
from prefab_ui.renderer import get_renderer_html
from test_skill_content_workflow import content_app

from app.core.config import settings
from app.services.app_content_service import AppDomainError
from app.services.app_publication_service import (
    publish_app_version,
    withdraw_app_version,
)
from app.services.app_registry_service import upload_app
from app.services.app_resource_registry import AppResourceRegistry
from app.services.app_resource_uri import AppResourceURI
from app.services.app_resources_provider import AppResourcesProvider
from app.storage.app_models import AppVersionRecord
from app.storage.runtime_identities import endpoint_identity, instance_identity
from app.storage.unit_of_work import UnitOfWork


@pytest.fixture
def resources(content_app, monkeypatch, tmp_path):
    _, database = content_app
    monkeypatch.setattr(settings, "mcpcat_apps_artifacts_path", str(tmp_path / "apps"))
    wire = {
        "view": {"type": "Heading", "content": "{{ message }}"},
        "state": {"message": "Hello"},
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("app.json", json.dumps(wire))
        archive.writestr("asset.bin", b"\xff\xfe\x00")
        archive.writestr("README.md", "# Dashboard")
    version_id = upload_app(
        database,
        slug="demo",
        display_name="Demo",
        version="1.0.0",
        content=output.getvalue(),
        filename="app.zip",
        entrypoint="app.json",
    )
    publish_app_version(database, slug="demo", version="1.0.0", reviewed=True)
    state = {"authorized": True, "available": True, "eligible": True}

    async def eligible(slug, version, info):
        return state["eligible"]

    registry = AppResourceRegistry(
        database,
        instance=instance_identity(database),
        endpoint=endpoint_identity(database, "apps"),
        authorize=lambda: state["authorized"],
        available=lambda: state["available"],
        eligible=eligible,
    )
    provider = AppResourcesProvider(registry)
    server = FastMCP("Apps resource prototype", providers=[provider])
    with UnitOfWork(database) as unit:
        app_id = unit.session.get(AppVersionRecord, version_id).app_id
    identity = AppResourceURI(registry.instance, registry.endpoint, app_id, "1.0.0")
    return database, state, registry, provider, server, identity


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_native_client_lists_and_reads_official_ui_and_original_files(
    resources, mode
):
    _, _, _, _, server, identity = resources
    async with Client(server, mode=mode) as client:
        listed = await client.list_resources()
        entries = {str(item.uri): item for item in listed}
        assert entries[identity.encode()].mime_type == UI_MIME_TYPE
        ui = await client.read_resource(identity.encode())
        assert ui[0].text == get_renderer_html("bundled")
        assert ui[0].mime_type == UI_MIME_TYPE
        assert ui[0].meta["ui"]["csp"]["resourceDomains"] == []
        markdown = await client.read_resource(
            replace(identity, path="README.md").encode()
        )
        assert markdown[0].text == "# Dashboard"
        binary = await client.read_resource(
            replace(identity, path="asset.bin").encode()
        )
        assert base64.b64decode(binary[0].blob) == b"\xff\xfe\x00"


@pytest.mark.asyncio
async def test_old_resource_object_cannot_read_after_key_or_source_revocation(
    resources,
):
    _, state, _, provider, _, identity = resources
    resource = await provider.get_resource(identity.encode())
    assert resource is not None
    for field in ["authorized", "available", "eligible"]:
        state[field] = False
        with pytest.raises(Exception, match="unavailable"):
            await resource.read()
        assert await provider.get_resource(identity.encode()) is None
        assert await provider.list_resources() == []
        state[field] = True


@pytest.mark.asyncio
async def test_withdraw_old_uri_and_other_endpoint_or_instance_refused(resources):
    database, _, _, provider, _, identity = resources
    assert (
        await provider.get_resource(
            replace(identity, endpoint="00000000-0000-4000-8000-000000000099").encode()
        )
        is None
    )
    assert (
        await provider.get_resource(
            replace(identity, instance="00000000-0000-4000-8000-000000000099").encode()
        )
        is None
    )
    withdraw_app_version(database, slug="demo", version="1.0.0")
    assert await provider.get_resource(identity.encode()) is None
    assert await provider.list_resources() == []


@pytest.mark.asyncio
async def test_draft_new_version_not_disclosed_and_old_uri_unchanged(resources):
    database, _, _, provider, server, identity = resources
    content = json.dumps({"view": {"type": "Heading", "content": "New"}}).encode()
    upload_app(
        database,
        slug="demo",
        display_name="Demo",
        version="1.1.0",
        content=content,
        filename="app.json",
    )
    assert (
        await provider.get_resource(replace(identity, version="1.1.0").encode()) is None
    )
    async with Client(server) as client:
        assert (await client.read_resource(identity.encode()))[
            0
        ].text == get_renderer_html("bundled")
    assert not any("1.1.0" in str(item.uri) for item in await provider.list_resources())


@pytest.mark.asyncio
async def test_revocation_during_payload_read_does_not_return_ui(resources):
    _, state, registry, _, _, identity = resources

    async def revoke(slug, version, info):
        state["authorized"] = False
        return True

    registry.eligible = revoke
    with pytest.raises(AppDomainError):
        await registry.entry(identity.encode())
