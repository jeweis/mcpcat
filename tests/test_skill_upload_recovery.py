"""整批上传失败清理不影响历史或其他共享制品。"""

from pathlib import Path

import pytest
from sqlalchemy import select
from test_skill_content_workflow import baseline, content_app, package

from app.core.config import settings
from app.services import skill_profile_upload
from app.services.skill_content_service import latest_variant, load_variant
from app.services.skill_registry_service import change_skill_lifecycle
from app.services.skill_variant_publication import publish_content_version
from app.storage.skill_variant_models import SkillRemoteArtifactRecord
from app.storage.unit_of_work import UnitOfWork


@pytest.mark.parametrize("selection", ["remote", "both"])
def test_failed_upload_discards_only_new_owned_bytes(
    content_app, monkeypatch, selection
):
    _, database = content_app
    _, original = baseline(database)
    root = Path(settings.mcpcat_artifacts_path)
    files_before = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*.zip")
    }
    real_store = skill_profile_upload.store_remote_variant

    def fail_after_remote_commit(*args, **kwargs):
        real_store(*args, **kwargs)
        raise RuntimeError("fixture-sensitive-message")

    monkeypatch.setattr(
        skill_profile_upload, "store_remote_variant", fail_after_remote_commit
    )
    with pytest.raises(RuntimeError):
        skill_profile_upload.upload_skill_profiles(
            database,
            content=package(b"# New content\n"),
            version="1.0.1",
            selection=selection,
        )
    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug("demo")
        assert unit.skill_versions.get(skill.id, "1.0.1") is None
        assert skill.latest_version == "1.0.0"
        assert list(unit.session.scalars(select(SkillRemoteArtifactRecord))) == []
        events = unit.skill_audit.list_for_skill("demo")
        assert events[-1].outcome == "failed"
        assert events[-1].details_json == {"error_type": "RuntimeError"}
        assert not any(
            event.action == "upload-variants" and event.outcome == "success"
            for event in events
        )
    assert {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*.zip")
    } == files_before
    _, content = load_variant(database, slug="demo", version="1.0.0", profile="local")
    assert content == original.normalized_zip


def test_lifecycle_preserves_frozen_bytes_and_blocks_archived_remote(content_app):
    _, database = content_app
    skill_profile_upload.upload_skill_profiles(
        database, content=package(), version="1.0.0", selection="both"
    )
    publish_content_version(database, slug="demo", version="1.0.0")
    _, original = load_variant(database, slug="demo", version="1.0.0", profile="remote")
    change_skill_lifecycle(
        database, slug="demo", version="1.0.0", action="deprecate", actor="fixture"
    )
    for profile in ("local", "remote"):
        assert latest_variant(database, slug="demo", profile=profile) == "1.0.0"
        _, content = load_variant(
            database, slug="demo", version="1.0.0", profile=profile
        )
        assert content == original
    change_skill_lifecycle(database, slug="demo", action="archive", actor="fixture")
    for profile in ("local", "remote"):
        assert latest_variant(database, slug="demo", profile=profile) is None
        with pytest.raises(ValueError, match="撤销"):
            load_variant(database, slug="demo", version="1.0.0", profile=profile)
    _, archived = load_variant(
        database, slug="demo", version="1.0.0", profile="remote", include_drafts=True
    )
    assert archived == original


def test_latest_excludes_published_content_with_corrupt_manifest(content_app):
    _, database = content_app
    _, version_id = skill_profile_upload.upload_skill_profiles(
        database, content=package(), version="1.0.0", selection="both"
    )
    publish_content_version(database, slug="demo", version="1.0.0")
    with UnitOfWork(database) as unit:
        variant = unit.skill_variants.get(version_id, "remote")
        files = variant.manifest_json["files"]
        variant.manifest_json = {"files": [{**files[0], "sha256": "0" * 64}]}
        unit.commit()
    assert latest_variant(database, slug="demo", profile="remote") is None
    assert latest_variant(database, slug="demo", profile="local") == "1.0.0"
    with pytest.raises(ValueError, match="清单"):
        load_variant(database, slug="demo", version="1.0.0", profile="remote")
