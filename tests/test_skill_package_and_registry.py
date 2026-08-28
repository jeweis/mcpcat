"""Agent Skill 包安全契约与上传生命周期应用服务测试。"""

from __future__ import annotations

import io
import stat
import zipfile
from pathlib import Path

import pytest

from app.core.config import settings
from app.services.skill_package_validator import (
    SkillPackageValidationError,
    validate_skill_zip,
)
from app.services.skill_registry_service import (
    change_skill_lifecycle,
    delete_draft_skill,
    publish_skill_version,
    upload_skill,
)
from app.storage.database import Database
from app.storage.migrations import upgrade_schema
from app.storage.skill_repositories import SkillDomainError
from app.storage.unit_of_work import UnitOfWork


def _skill_markdown(
    name: str = "demo-skill", *, compatibility: str | None = "Codex >= 1.0"
) -> bytes:
    lines = ["---", f"name: {name}", "description: Use this Skill for demo work."]
    if compatibility is not None:
        lines.append(f"compatibility: {compatibility}")
    lines.extend(
        ["metadata:", "  author: mcpcat", "allowed-tools: Read Bash", "---", "# Demo"]
    )
    return ("\n".join(lines) + "\n").encode()


def _zip_bytes(entries: list[tuple[str, bytes | zipfile.ZipInfo]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            if isinstance(content, zipfile.ZipInfo):
                archive.writestr(content, name.encode())
            else:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, content)
    return output.getvalue()


def _valid_zip(
    *,
    name: str = "demo-skill",
    extra_files: list[tuple[str, bytes | zipfile.ZipInfo]] | None = None,
) -> bytes:
    return _zip_bytes(
        [(f"{name}/SKILL.md", _skill_markdown(name))] + (extra_files or [])
    )


@pytest.fixture
def database(tmp_path: Path) -> Database:
    value = Database(tmp_path / "skills-registry.db")
    upgrade_schema(value)
    yield value
    value.dispose()


def _audit_actions(database: Database, slug: str) -> list[tuple[str, str, str | None]]:
    with UnitOfWork(database) as unit:
        return [
            (event.action, event.outcome, event.actor)
            for event in unit.skill_audit.list_for_skill(slug)
        ]


def test_accepts_agent_skills_frontmatter_contract() -> None:
    validation = validate_skill_zip(_valid_zip())

    assert validation.name == "demo-skill"
    assert validation.description == "Use this Skill for demo work."
    assert validation.compatibility == "Codex >= 1.0"
    assert validation.metadata["metadata"] == {"author": "mcpcat"}
    assert validation.files[0].path == "demo-skill/SKILL.md"
    assert validation.scripts == []


@pytest.mark.parametrize(
    ("frontmatter", "message"),
    [
        ("name: demo-skill\n", "description"),
        ("name: Demo-Skill\ndescription: valid\n", "slug"),
        ("name: another-skill\ndescription: valid\n", "根目录一致"),
        ("name: demo-skill\ndescription: \n", "非空字符串"),
        (
            "name: demo-skill\ndescription: valid\ncompatibility: [bad]\n",
            "compatibility",
        ),
        ("name: demo-skill\ndescription: valid\nmetadata: {author: 7}\n", "metadata"),
        (
            "name: demo-skill\ndescription: valid\nallowed-tools: [Read]\n",
            "allowed-tools",
        ),
    ],
)
def test_rejects_invalid_agent_skills_frontmatter(
    frontmatter: str, message: str
) -> None:
    payload = ("---\n" + frontmatter + "---\n# Demo\n").encode()

    with pytest.raises(ValueError, match=message):
        validate_skill_zip(_zip_bytes([("demo-skill/SKILL.md", payload)]))


def test_rejects_missing_frontmatter_and_missing_skill_file() -> None:
    with pytest.raises(SkillPackageValidationError, match="frontmatter"):
        validate_skill_zip(_zip_bytes([("demo-skill/SKILL.md", b"# no header\n")]))
    with pytest.raises(SkillPackageValidationError, match="SKILL.md"):
        validate_skill_zip(_zip_bytes([("demo-skill/README.md", b"content")]))


def test_accepts_standard_library_default_regular_zip_entries() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("demo-skill/SKILL.md", _skill_markdown())

    assert validate_skill_zip(output.getvalue()).name == "demo-skill"


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "/absolute",
        "C:/absolute",
        "demo-skill\\SKILL.md",
        "demo-skill/../SKILL.md",
    ],
)
def test_rejects_path_traversal_absolute_windows_and_backslash_paths(path: str) -> None:
    with pytest.raises(SkillPackageValidationError, match="路径|绝对路径"):
        validate_skill_zip(_zip_bytes([(path, b"unsafe")]))


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        (stat.S_IFLNK | 0o777, "符号链接"),
        (stat.S_IFCHR | 0o600, "设备文件"),
    ],
)
def test_rejects_symbolic_links_and_device_files(mode: int, message: str) -> None:
    info = zipfile.ZipInfo("demo-skill/unsafe")
    info.create_system = 3
    info.external_attr = mode << 16

    with pytest.raises(SkillPackageValidationError, match=message):
        validate_skill_zip(_zip_bytes([("demo-skill/unsafe", info)]))


def test_rejects_multiple_skill_roots_and_duplicate_paths() -> None:
    with pytest.raises(SkillPackageValidationError, match="一个 Skill 根目录"):
        validate_skill_zip(
            _zip_bytes(
                [
                    ("demo-skill/SKILL.md", _skill_markdown()),
                    ("other-skill/README.md", b"extra root"),
                ]
            )
        )
    with pytest.raises(SkillPackageValidationError, match="重复规范化路径"):
        validate_skill_zip(
            _zip_bytes(
                [
                    ("demo-skill/SKILL.md", _skill_markdown()),
                    ("demo-skill/SKILL.md", _skill_markdown()),
                ]
            )
        )


def test_enforces_zip_expanded_single_file_and_file_count_limits(monkeypatch) -> None:
    payload = _valid_zip(extra_files=[("demo-skill/data.txt", b"x" * 32)])
    monkeypatch.setattr(settings, "mcpcat_skill_zip_max_bytes", len(payload) - 1)
    with pytest.raises(SkillPackageValidationError, match="ZIP 大小"):
        validate_skill_zip(payload)

    monkeypatch.setattr(settings, "mcpcat_skill_zip_max_bytes", len(payload) + 1)
    monkeypatch.setattr(settings, "mcpcat_skill_expanded_max_bytes", 1)
    with pytest.raises(SkillPackageValidationError, match="展开总大小"):
        validate_skill_zip(payload)

    monkeypatch.setattr(settings, "mcpcat_skill_expanded_max_bytes", 10_000)
    monkeypatch.setattr(settings, "mcpcat_skill_file_max_bytes", 16)
    with pytest.raises(SkillPackageValidationError, match="单个文件"):
        validate_skill_zip(payload)

    monkeypatch.setattr(settings, "mcpcat_skill_file_max_bytes", 10_000)
    monkeypatch.setattr(settings, "mcpcat_skill_max_files", 1)
    with pytest.raises(SkillPackageValidationError, match="文件数量"):
        validate_skill_zip(payload)


def test_normalizes_zip_deterministically_and_only_reports_scripts(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "script-ran"
    extra = [
        ("demo-skill/scripts/run.sh", f"touch {marker}".encode()),
        ("demo-skill/tools/build.py", b"print('never executed')"),
        ("demo-skill/references/guide.md", b"documentation"),
    ]
    first = validate_skill_zip(_valid_zip(extra_files=extra))
    second = validate_skill_zip(_valid_zip(extra_files=list(reversed(extra))))

    assert first.normalized_zip == second.normalized_zip
    assert first.sha256 == second.sha256
    assert [entry.path for entry in first.files] == sorted(
        entry.path for entry in first.files
    )
    assert first.scripts == ["demo-skill/scripts/run.sh", "demo-skill/tools/build.py"]
    assert not marker.exists()


def test_upload_creates_draft_artifact_manifest_and_audit(
    database: Database, tmp_path: Path
) -> None:
    version_id, validation = upload_skill(
        database,
        content=_valid_zip(extra_files=[("demo-skill/scripts/check.py", b"pass")]),
        version="1.0.0",
        changelog="initial draft",
        actor="alice",
        artifact_root=tmp_path / "artifacts",
    )

    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug("demo-skill")
        version = unit.skill_versions.get(skill.id, "1.0.0")
        artifact = unit.skill_artifacts.get_for_version(version_id)
        assert skill.source_type == "uploaded"
        assert skill.status == "draft"
        assert version.status == "draft"
        assert version.changelog == "initial draft"
        assert version.source_snapshot_json["scripts"] == [
            "demo-skill/scripts/check.py"
        ]
        assert artifact.sha256 == validation.sha256
        assert (
            tmp_path / "artifacts" / artifact.relative_path
        ).read_bytes() == validation.normalized_zip
    assert _audit_actions(database, "demo-skill") == [("upload", "success", "alice")]


def test_publish_lifecycle_and_auditing(database: Database, tmp_path: Path) -> None:
    upload_skill(
        database,
        content=_valid_zip(),
        version="1.0.0",
        changelog="initial",
        actor="alice",
        artifact_root=tmp_path / "artifacts",
    )
    publish_skill_version(database, slug="demo-skill", version="1.0.0", actor="bob")
    change_skill_lifecycle(
        database, slug="demo-skill", action="deprecate", actor="carol"
    )
    change_skill_lifecycle(database, slug="demo-skill", action="archive", actor="dave")

    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug("demo-skill")
        version = unit.skill_versions.get(skill.id, "1.0.0")
        assert skill.status == "archived"
        assert skill.visibility == "private"
        assert version.status == "published"
        assert version.published_at is not None
    assert _audit_actions(database, "demo-skill") == [
        ("upload", "success", "alice"),
        ("publish", "success", "bob"),
        ("deprecate", "success", "carol"),
        ("archive", "success", "dave"),
    ]


def test_publish_without_artifact_records_failure_audit(database: Database) -> None:
    with UnitOfWork(database) as unit:
        skill = unit.skills.create(
            slug="demo-skill",
            display_name="demo-skill",
            description="demo",
            source_type="uploaded",
        )
        unit.skill_versions.create(skill=skill, version="1.0.0")
        unit.commit()

    with pytest.raises(SkillDomainError, match="完整可发布制品"):
        publish_skill_version(database, slug="demo-skill", version="1.0.0", actor="bob")
    assert _audit_actions(database, "demo-skill") == [("publish", "failed", "bob")]


def test_safe_deletion_only_allows_unpublished_drafts_and_removes_artifact(
    database: Database, tmp_path: Path
) -> None:
    artifact_root = tmp_path / "artifacts"
    upload_skill(
        database,
        content=_valid_zip(),
        version="1.0.0",
        changelog="draft",
        actor="alice",
        artifact_root=artifact_root,
    )
    artifact_path = artifact_root / "demo-skill" / "1.0.0.zip"
    assert artifact_path.exists()

    delete_draft_skill(
        database, slug="demo-skill", actor="bob", artifact_root=artifact_root
    )
    assert not artifact_path.exists()
    with UnitOfWork(database) as unit:
        assert unit.skills.get_by_slug("demo-skill") is None
    assert _audit_actions(database, "demo-skill") == [
        ("upload", "success", "alice"),
        ("delete", "success", "bob"),
    ]

    upload_skill(
        database,
        content=_valid_zip(),
        version="1.0.0",
        changelog="replacement",
        actor="alice",
        artifact_root=artifact_root,
    )
    publish_skill_version(database, slug="demo-skill", version="1.0.0", actor="alice")
    with pytest.raises(SkillDomainError, match="不可物理删除"):
        delete_draft_skill(
            database, slug="demo-skill", actor="bob", artifact_root=artifact_root
        )
    assert (artifact_root / "demo-skill" / "1.0.0.zip").exists()
