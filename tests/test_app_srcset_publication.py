"""新响应式资产发布与旧冻结装配版本不变。"""

import io
import zipfile

import pytest
from sqlalchemy import select
from test_app_management_api import apps_client, upload
from test_skill_content_workflow import READ, WRITE, content_app

from app.services.app_content_service import load_app_content
from app.services.app_rendering import render_app
from app.storage.app_models import AppVersionRecord
from app.storage.unit_of_work import UnitOfWork


def package() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "index.html", '<link rel="preload" as="image" imagesrcset="a.png 1x">'
        )
        archive.writestr("a.png", b"fixture-image")
    return output.getvalue()


@pytest.mark.asyncio
async def test_new_publication_freezes_current_assembly_and_original_download(
    apps_client,
):
    client, database = apps_client
    raw = package()
    async with client:
        assert (
            await upload(client, content=raw, filename="app.zip")
        ).status_code == 200
        response = await client.post(
            "/api/apps/demo/versions/1.0.0/publish",
            headers={"Mcpcat-Key": WRITE},
            json={"reviewed": True},
        )
        assert response.status_code == 200, response.text
        version = response.json()["versions"][0]
        assert version["rendering_version"] == "html-assembly-5"
        preview = await client.get(
            "/api/apps/demo/versions/1.0.0/preview",
            headers={"Mcpcat-Key": READ},
        )
        assert preview.status_code == 200, preview.text
        assert "data:image/png;base64," in preview.json()["html"]
        policy = preview.json()["requested_metadata"]["ui"]["csp"]
        assert policy["resourceDomains"] == ["data:"]
        assert not policy.get("connectDomains")
        downloaded = await client.get(
            "/api/apps/demo/versions/1.0.0/download",
            headers={"Mcpcat-Key": READ},
        )
        assert downloaded.content == raw


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frozen,assembly",
    [
        (False, "html-assembly-1"),
        (True, "html-assembly-1"),
        (True, "html-assembly-2"),
        (True, "html-assembly-3"),
        (True, "html-assembly-4"),
    ],
)
async def test_old_published_assembly_never_silently_changes(
    apps_client, frozen, assembly
):
    client, database = apps_client
    raw = package()
    async with client:
        await upload(client, content=raw, filename="app.zip")
    info, content = load_app_content(
        database, slug="demo", version="1.0.0", include_drafts=True
    )
    with UnitOfWork(database) as unit:
        row = unit.session.scalar(select(AppVersionRecord))
        app_id = row.app_id
        old = render_app({**info, "rendering_version": assembly}, content, app_id)
        if assembly == "html-assembly-1":
            assert old.html == '<link rel="preload" as="image" imagesrcset="a.png 1x">'
        else:
            assert "data:image/png;base64," in old.html
        row.status = "published"
        unit.apps.get("demo").status = "published"
        if frozen:
            row.rendering_version = old.renderer_version
            row.rendered_sha256 = old.sha256
            row.rendered_payload_sha256 = old.payload_sha256()
        unit.commit()
    restored, content = load_app_content(database, slug="demo", version="1.0.0")
    current = render_app(restored, content, app_id)
    assert current == old
    assert content == raw
    with pytest.raises(ValueError, match="不受支持"):
        render_app(
            {**restored, "rendering_version": "html-assembly-future"}, content, app_id
        )
