"""remote 制品删除与诊断回归，隔离数据库和磁盘文件。"""

from pathlib import Path

import pytest
from sqlalchemy import select
from test_skill_content_workflow import content_app as fixture_content_app
from test_skill_content_workflow import package

from app.core.config import settings
from app.services.skill_artifact_service import diagnose_artifacts
from app.services.skill_content_service import store_remote_variant
from app.services.skill_profile_upload import (
    _discard_incomplete_draft,
    upload_skill_profiles,
)
from app.services.skill_registry_service import delete_draft_skill
from app.services.skill_variant_publication import publish_content_version
from app.storage.skill_variant_models import SkillRemoteArtifactRecord
from app.storage.unit_of_work import UnitOfWork

content_app = fixture_content_app


def remote_artifact(database):
    with UnitOfWork(database) as unit:
        row = unit.session.scalar(select(SkillRemoteArtifactRecord))
        return row.id, Path(settings.mcpcat_artifacts_path) / row.relative_path


@pytest.mark.parametrize("selection", ["local", "remote", "both", "different"])
def test_delete_draft_cleans_all_owned_artifacts(content_app, selection):
    _, database = content_app
    _, version_id = upload_skill_profiles(
        database,
        content=package(),
        version="1.0.0",
        selection="local" if selection == "different" else selection,
    )
    if selection == "different":
        store_remote_variant(
            database, version_id=version_id, content=package(b"# Remote\n")
        )
    root = Path(settings.mcpcat_artifacts_path)
    assert list(root.rglob("*.zip"))
    assert diagnose_artifacts(database)["ok"] is True
    delete_draft_skill(database, slug="demo", actor="fixture")
    with UnitOfWork(database) as unit:
        assert unit.skills.get_by_slug("demo") is None
        assert list(unit.session.scalars(select(SkillRemoteArtifactRecord))) == []
    assert list(root.rglob("*.zip")) == []


def test_failed_delete_commit_preserves_remote_bytes(content_app, monkeypatch):
    _, database = content_app
    upload_skill_profiles(
        database, content=package(), version="1.0.0", selection="remote"
    )
    artifact_id, path = remote_artifact(database)
    original = path.read_bytes()

    def fail_commit(self):
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(UnitOfWork, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="commit failure"):
        delete_draft_skill(database, slug="demo", actor="fixture")
    with UnitOfWork(database) as unit:
        assert unit.skills.get_by_slug("demo") is not None
        assert unit.skill_variants.get_remote_artifact(artifact_id) is not None
    assert path.read_bytes() == original


def test_shared_published_remote_survives_draft_cleanup(content_app):
    _, database = content_app
    upload_skill_profiles(
        database, content=package(), version="1.0.0", selection="remote"
    )
    publish_content_version(database, slug="demo", version="1.0.0")
    artifact_id, path = remote_artifact(database)
    original = path.read_bytes()
    _, new_id = upload_skill_profiles(
        database, content=package(), version="1.0.1", selection="remote"
    )
    with UnitOfWork(database) as unit:
        assert (
            unit.skill_variants.get(new_id, "remote").remote_artifact_id == artifact_id
        )
    _discard_incomplete_draft(database, new_id)
    with pytest.raises(ValueError, match="已发布历史"):
        delete_draft_skill(database, slug="demo", actor="fixture")
    with UnitOfWork(database) as unit:
        assert unit.skill_variants.get_remote_artifact(artifact_id) is not None
    assert path.read_bytes() == original


def test_delete_draft_preserves_remote_referenced_by_other_skill(content_app):
    _, database = content_app
    upload_skill_profiles(
        database, content=package(), version="1.0.0", selection="remote"
    )
    artifact_id, path = remote_artifact(database)
    with UnitOfWork(database) as unit:
        other = unit.skills.create(
            slug="other",
            display_name="Other",
            description="Other",
            source_type="uploaded",
        )
        version = unit.skill_versions.create(skill=other, version="1.0.0")
        unit.session.flush()
        unit.skill_variants.create(
            version_id=version.id, profile="remote", remote_artifact_id=artifact_id
        )
        unit.commit()
    delete_draft_skill(database, slug="demo", actor="fixture")
    assert path.is_file()
    with UnitOfWork(database) as unit:
        assert unit.skill_variants.get_remote_artifact(artifact_id) is not None
    delete_draft_skill(database, slug="other", actor="fixture")
    assert not path.exists()


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_diagnosis_checks_remote_and_can_recover(content_app, damage):
    _, database = content_app
    upload_skill_profiles(
        database, content=package(), version="1.0.0", selection="remote"
    )
    artifact_id, path = remote_artifact(database)
    original = path.read_bytes()
    assert diagnose_artifacts(database) == {"ok": True, "problems": [], "temporary": []}
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"x" * len(original))
    result = diagnose_artifacts(database)
    assert result["ok"] is False
    assert result["problems"] == [
        {
            "relative_path": path.relative_to(
                Path(settings.mcpcat_artifacts_path)
            ).as_posix(),
            "status": damage,
        }
    ]
    with UnitOfWork(database) as unit:
        assert (
            unit.skill_variants.get_remote_artifact(artifact_id).integrity_status
            == damage
        )
    path.write_bytes(original)
    assert diagnose_artifacts(database)["ok"] is True
    with UnitOfWork(database) as unit:
        assert (
            unit.skill_variants.get_remote_artifact(artifact_id).integrity_status
            == "ok"
        )
