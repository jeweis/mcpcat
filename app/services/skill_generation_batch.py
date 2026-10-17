"""同一来源快照生成完整模式批次，所有入口共用不可变版本逻辑。"""

import hashlib
from pathlib import Path
from typing import Any

from filelock import FileLock
from sqlalchemy import text

from app.core.config import settings
from app.services import mcp_skill_generator as generator
from app.services.skill_content_locks import version_content_lock
from app.services.skill_content_service import load_variant, store_remote_variant
from app.services.skill_package_validator import (
    build_deterministic_skill_zip,
    validate_skill_zip,
)
from app.services.skill_profile_upload import _discard_incomplete_draft
from app.services.skill_template_renderer import (
    LOCAL_TEMPLATE_VERSION,
    REMOTE_TEMPLATE_VERSION,
)
from app.services.skill_variant_artifacts import inspect_variant_zip
from app.storage.database import Database
from app.storage.runtime_identities import current_endpoint_identity, endpoint_identity
from app.storage.skill_repositories import SkillDomainError
from app.storage.skill_variant_models import RuntimeIdentityRecord
from app.storage.unit_of_work import UnitOfWork


def generate_skill_batch(
    database: Database,
    *,
    snapshot: dict[str, Any],
    slug: str,
    source_ref: dict,
    actor: str | None,
    effective_base_url: str | None,
    selection: str = "local",
    artifact_root: Path | None = None,
    source_binding: str | None = None,
) -> generator.GeneratedSkillResult:
    """预先校验全部内容；模式/绑定/实际字节共同决定幂等键。"""
    if selection not in {"local", "remote", "both"}:
        raise SkillDomainError("不支持的生成模式")
    profiles = ["local", "remote"] if selection == "both" else [selection]
    kind = "catalog" if snapshot["source_kind"] == "catalog" else "service"
    binding = source_binding or endpoint_identity(
        database, kind, snapshot["server_name"] if kind == "service" else ""
    )
    packages = _render_packages(snapshot, slug, profiles, effective_base_url)
    context = {
        "generator_version": generator.GENERATOR_VERSION,
        "tool_schema_hash": generator._schema_hash(snapshot),
        "source_binding": binding,
        "variants": {
            profile: {
                "sha256": package.sha256,
                "binding": binding if profile == "remote" else "",
            }
            for profile, package in packages.items()
        },
    }
    lock = FileLock(
        str(database.path)
        + ".skill-generation-"
        + hashlib.sha256(slug.encode()).hexdigest()
        + ".lock",
        timeout=max(1, settings.mcpcat_sqlite_busy_timeout_ms / 1000),
    )
    with lock:
        if (
            current_endpoint_identity(
                database, kind, snapshot["server_name"] if kind == "service" else ""
            )
            != binding
        ):
            raise SkillDomainError("生成过程中来源已变更，请重新生成")
        existing = _matching_batch(database, slug, context, artifact_root)
        if existing:
            return existing
        version_id, version = _create_batch(
            database,
            slug,
            snapshot,
            source_ref,
            packages,
            context,
            actor,
            effective_base_url,
        )
        with version_content_lock(database, version_id):
            try:
                _store_packages(
                    database, version_id, packages, snapshot, binding, artifact_root
                )
                _record_success(database, slug, snapshot, version, actor, profiles)
            except Exception as error:
                _discard_incomplete_draft(
                    database, version_id, artifact_root=artifact_root
                )
                _record_failure(database, slug, actor, error)
                raise
        return generator.GeneratedSkillResult(
            slug, version, version_id, True, generator._schema_hash(snapshot)
        )


def _render_packages(snapshot, slug, profiles, base):
    if "local" in profiles and not base:
        raise SkillDomainError("无法确定 MCP Skill 的 mcpcat 访问地址")
    packages = {}
    for profile in profiles:
        files = generator._package_files(
            slug=slug, snapshot=snapshot, public_base_url=base or "", profile=profile
        )
        package = validate_skill_zip(build_deterministic_skill_zip(files))
        if profile == "remote":
            inspect_variant_zip(package.normalized_zip, remote=True)
        packages[profile] = package
    return packages


def _matching_batch(database, slug, context, root):
    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug(slug)
        if skill is None or skill.status == "archived":
            return None
        if skill.source_type != "mcp-generated":
            raise SkillDomainError("同名 Skill 已由其他来源占用")
        for version in unit.skill_versions.list_for_skill(skill.id):
            if (
                version.generator_version != context["generator_version"]
                or version.tool_schema_hash != context["tool_schema_hash"]
            ):
                continue
            if version.source_snapshot_json.get("generation_batch") != context:
                continue
            # 不能拿历史制品损坏当作生成成功，也不能复用已撤销的 remote。
            for profile, expected in context["variants"].items():
                variant = unit.skill_variants.get(
                    version.id, profile, expected["binding"]
                )
                if variant is None or variant.status == "archived":
                    break
                info, _ = load_variant(
                    database,
                    slug=slug,
                    version=version.version,
                    profile=profile,
                    binding=expected["binding"],
                    include_drafts=version.status == "draft",
                    artifact_root=root,
                )
                if info["sha256"] != expected["sha256"]:
                    raise SkillDomainError("已有生成批次制品不一致，请先恢复制品")
            else:
                return generator.GeneratedSkillResult(
                    slug, version.version, version.id, False, version.tool_schema_hash
                )
    return None


def _create_batch(database, slug, snapshot, source_ref, packages, context, actor, base):
    with UnitOfWork(database) as unit:
        unit.session.execute(text("BEGIN IMMEDIATE"))
        kind = "catalog" if snapshot["source_kind"] == "catalog" else "service"
        key = (
            "endpoint:catalog"
            if kind == "catalog"
            else "endpoint:service:" + snapshot["server_name"]
        )
        identity = unit.session.get(RuntimeIdentityRecord, key)
        if identity is None or identity.value != context["source_binding"]:
            raise SkillDomainError("生成过程中来源已变更，请重新生成")
        skill = unit.skills.get_by_slug(slug)
        if skill is None:
            skill = unit.skills.create(
                slug=slug,
                display_name=snapshot["display_name"],
                description=generator._description(snapshot),
                source_type="mcp-generated",
                source_ref=source_ref,
                created_by=actor,
            )
        elif skill.source_type != "mcp-generated":
            raise SkillDomainError("同名 Skill 已由其他来源占用")
        version = generator._next_version(skill.latest_version)
        package = packages.get("local") or packages["remote"]
        local_context = (
            generator._generation_context(snapshot, base) if "local" in packages else {}
        )
        row = unit.skill_versions.create(
            skill=skill,
            version=version,
            created_by=actor,
            generator_version=generator.GENERATOR_VERSION,
            tool_schema_hash=generator._schema_hash(snapshot),
            compatibility=(
                {
                    "node": generator.NODE_COMPATIBILITY,
                    "mcporter": generator.MCPORTER_VERSION,
                }
                if "local" in packages
                else {"remote_skills": True}
            ),
            source_snapshot={
                **source_ref,
                **local_context,
                "generation_batch": context,
                "content_profiles": list(packages),
                "source_kind": snapshot["source_kind"],
                "generated_description": generator._description(snapshot),
                "instructions": snapshot["instructions"],
                "tools": snapshot["tools"],
                "files": [entry.__dict__ for entry in package.files],
                "scripts": package.scripts,
            },
        )
        unit.session.flush()
        unit.commit()
        return row.id, version


def _store_packages(database, version_id, packages, snapshot, binding, root):
    for profile, package in packages.items():
        metadata = {
            "generator_version": generator.GENERATOR_VERSION,
            "profile": profile,
            "template_version": (
                LOCAL_TEMPLATE_VERSION
                if profile == "local"
                else REMOTE_TEMPLATE_VERSION
            ),
            "tool_schema_hash": generator._schema_hash(snapshot),
            "binding_kind": (
                "catalog" if snapshot["source_kind"] == "catalog" else "service"
            ),
            "source_name": snapshot["server_name"],
            "required_tools": [tool["name"] for tool in snapshot["tools"]],
        }
        if profile == "local":
            kwargs = {"artifact_root": root} if root is not None else {}
            generator.store_version_artifact(
                database,
                skill_version_id=version_id,
                content=package.normalized_zip,
                **kwargs,
            )
            with UnitOfWork(database) as unit:
                unit.skill_variants.get(version_id, "local").metadata_json = metadata
                unit.commit()
        else:
            store_remote_variant(
                database,
                version_id=version_id,
                content=package.normalized_zip,
                binding=binding,
                metadata=metadata,
                artifact_root=root,
            )


def _record_success(database, slug, snapshot, version, actor, profiles):
    with UnitOfWork(database) as unit:
        skill = unit.skills.get_by_slug(slug)
        unit.skills.update_metadata(
            skill,
            display_name=snapshot["display_name"],
            description=generator._description(snapshot),
        )
        unit.skill_audit.record(
            skill_id=skill.id,
            skill_slug=slug,
            version=version,
            action="generate",
            actor=actor,
            outcome="success",
            details={
                "profiles": profiles,
                "tool_schema_hash": generator._schema_hash(snapshot),
            },
        )
        unit.commit()


def _record_failure(database, slug, actor, error):
    with UnitOfWork(database) as unit:
        unit.skill_audit.record(
            skill_slug=slug,
            action="generate",
            actor=actor,
            outcome="failed",
            details={"error_type": type(error).__name__},
        )
        unit.commit()
