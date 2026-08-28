"""Skills Registry 管理 API。"""

from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.middleware.auth import get_current_user
from app.services.config_service import ConfigService
from app.services.mcp_skill_generator import generate_mcp_skill
from app.services.security_service import security_service
from app.services.skill_distribution_service import (
    MIN_CLI_VERSION,
    RECOMMENDED_CLI_VERSION,
    REGISTRY_API_VERSION,
    REGISTRY_SCHEMA_VERSION,
    build_latest_bundle,
    build_registry_index,
    resolve_bundle_download,
    resolve_version_download,
    stable_etag,
)
from app.services.skill_registry_service import (
    change_skill_lifecycle,
    delete_draft_skill,
    publish_skill_version,
    replace_draft_package,
    upload_skill,
)
from app.storage.skill_repositories import SkillDomainError, semver_key
from app.storage.unit_of_work import UnitOfWork

router = APIRouter()


def _actor(request: Request) -> str | None:
    user = get_current_user(request) or {}
    return user.get("name")


def _base_url(request: Request) -> str:
    return ConfigService.get_public_base_url() or str(request.base_url).rstrip("/")


def _include_drafts(request: Request) -> bool:
    user = get_current_user(request) or {}
    return user.get("permission") == "write"


def _check_cli_compatibility(request: Request) -> None:
    declared = request.headers.get("X-Mcpcat-CLI-Version")
    if not declared:
        return
    try:
        incompatible = semver_key(declared) < semver_key(MIN_CLI_VERSION)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="无效的 CLI 版本") from error
    if incompatible:
        raise HTTPException(
            status_code=426,
            detail={
                "message": "CLI 版本不兼容，请升级后重试",
                "min_cli_version": MIN_CLI_VERSION,
                "recommended_cli_version": RECOMMENDED_CLI_VERSION,
            },
        )


def _tool_schema_diff(current: dict, previous: dict | None) -> dict:
    current_tools = {
        item.get("name"): item
        for item in (current.get("tools") or [])
        if isinstance(item, dict) and item.get("name")
    }
    previous_tools = {
        item.get("name"): item
        for item in ((previous or {}).get("tools") or [])
        if isinstance(item, dict) and item.get("name")
    }
    added = sorted(set(current_tools) - set(previous_tools))
    removed = sorted(set(previous_tools) - set(current_tools))
    changed = sorted(
        name
        for name in set(current_tools).intersection(previous_tools)
        if json.dumps(current_tools[name], sort_keys=True)
        != json.dumps(previous_tools[name], sort_keys=True)
    )
    return {"added": added, "removed": removed, "changed": changed}


@router.get("/skills/bootstrap")
async def skills_bootstrap(request: Request):
    """CLI 无需猜测 /api 路径或认证头的发现入口。"""

    base_url = _base_url(request)
    return {
        "instance_name": request.app.title,
        "base_url": base_url,
        "api_version": REGISTRY_API_VERSION,
        "registry_schema_version": REGISTRY_SCHEMA_VERSION,
        "auth_header_name": security_service.get_auth_header_name(),
        "registry_path": "/api/skills/registry",
        "min_cli_version": MIN_CLI_VERSION,
        "recommended_cli_version": RECOMMENDED_CLI_VERSION,
    }


@router.get("/skills/registry")
async def registry_index(request: Request):
    _check_cli_compatibility(request)
    payload = build_registry_index(
        request.app.state.database, base_url=_base_url(request)
    )
    etag = stable_etag(payload)
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(payload, headers={"ETag": etag})


@router.get("/skills")
async def list_skills(request: Request):
    """按调用者权限列出可见 Skill。"""

    include_drafts = _include_drafts(request)
    with UnitOfWork(request.app.state.database) as unit:
        rows = unit.skills.list_visible(include_drafts=include_drafts)
        return [
            {
                "slug": row.slug,
                "display_name": row.display_name,
                "description": row.description,
                "source_type": row.source_type,
                "source": row.source_ref_json,
                "status": row.status,
                "latest_version": row.latest_version if include_drafts else None,
                "latest_published_version": row.latest_published_version,
                "updated_at": row.updated_at.isoformat(),
            }
            for row in rows
        ]


@router.get("/skills/{slug}/versions/{version}/download")
async def download_skill_version(slug: str, version: str, request: Request):
    try:
        artifact = resolve_version_download(
            request.app.state.database,
            slug=slug,
            version=version,
            include_drafts=_include_drafts(request),
        )
    except SkillDomainError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    etag = f'"{artifact.sha256}"'
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return FileResponse(
        artifact.path,
        media_type="application/zip",
        filename=f"{slug}-{version}.zip",
        headers={
            "ETag": etag,
            "X-Skill-Version": version,
            "X-Checksum-Sha256": artifact.sha256,
        },
    )


@router.get("/skills/bundle")
async def download_latest_bundle(request: Request):
    try:
        bundle = build_latest_bundle(
            request.app.state.database, base_url=_base_url(request)
        )
    except SkillDomainError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return FileResponse(
        bundle.path,
        media_type="application/zip",
        filename=f"mcpcat-skills-{bundle.id}.zip",
        headers={
            "ETag": f'"{bundle.sha256}"',
            "X-Bundle-Id": str(bundle.id),
            "X-Checksum-Sha256": bundle.sha256,
        },
    )


@router.get("/skills/bundles/{bundle_id}")
async def download_historical_bundle(bundle_id: int, request: Request):
    try:
        bundle = resolve_bundle_download(
            request.app.state.database, bundle_id=bundle_id
        )
    except SkillDomainError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return FileResponse(
        bundle.path,
        media_type="application/zip",
        filename=f"mcpcat-skills-{bundle.id}.zip",
        headers={
            "ETag": f'"{bundle.sha256}"',
            "X-Bundle-Id": str(bundle.id),
            "X-Checksum-Sha256": bundle.sha256,
        },
    )


@router.get("/skills/{slug}")
async def get_skill_detail(slug: str, request: Request):
    include_drafts = _include_drafts(request)
    with UnitOfWork(request.app.state.database) as unit:
        skill = unit.skills.get_by_slug(slug)
        if skill is None or (
            not include_drafts
            and (skill.visibility != "published" or skill.status == "archived")
        ):
            raise HTTPException(status_code=404, detail="Skill 不存在")
        versions = unit.skill_versions.list_for_skill(skill.id)
        visible_versions = [
            version
            for version in versions
            if include_drafts or version.status in {"published", "deprecated"}
        ]
        version_payloads = []
        for index, version in enumerate(visible_versions):
            artifact = unit.skill_artifacts.get_for_version(version.id)
            snapshot = version.source_snapshot_json or {}
            previous_snapshot = (
                visible_versions[index + 1].source_snapshot_json
                if index + 1 < len(visible_versions)
                else None
            )
            version_payloads.append(
                {
                    "version": version.version,
                    "status": version.status,
                    "changelog": version.changelog,
                    "compatibility": version.compatibility_json,
                    "tool_schema_hash": version.tool_schema_hash,
                    "schema_diff": _tool_schema_diff(snapshot, previous_snapshot),
                    "generator_version": version.generator_version,
                    "created_at": version.created_at.isoformat(),
                    "published_at": (
                        version.published_at.isoformat()
                        if version.published_at
                        else None
                    ),
                    "artifact": (
                        {
                            "sha256": artifact.sha256,
                            "size": artifact.size,
                            "integrity_status": artifact.integrity_status,
                        }
                        if artifact is not None
                        else None
                    ),
                    "files": snapshot.get("files") or [],
                    "scripts": snapshot.get("scripts") or [],
                    "source_snapshot": snapshot if include_drafts else None,
                }
            )
        return {
            "slug": skill.slug,
            "display_name": skill.display_name,
            "description": skill.description,
            "source_type": skill.source_type,
            "source": skill.source_ref_json,
            "status": skill.status,
            "updated_at": skill.updated_at.isoformat(),
            "versions": version_payloads,
        }


@router.post("/servers/{server_name}/skill")
async def generate_server_skill(server_name: str, request: Request):
    """生成或刷新一个 MCP 服务对应的待发布 Skill。"""

    try:
        result = await generate_mcp_skill(
            request.app.state.database,
            manager=request.app.state.server_manager,
            server_name=server_name,
            actor=_actor(request),
        )
    except SkillDomainError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return result.__dict__


@router.post("/skills/upload")
async def upload_skill_zip(
    request: Request,
    file: UploadFile = File(...),
    version: str = Form(...),
    changelog: str = Form(""),
):
    """上传普通 Agent Skill ZIP 并创建草稿版本。"""

    content = await file.read()
    try:
        version_id, validation = upload_skill(
            request.app.state.database,
            content=content,
            version=version,
            changelog=changelog,
            actor=_actor(request),
        )
    except SkillDomainError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {
        "slug": validation.name,
        "version": version,
        "version_id": version_id,
        "status": "draft",
        "sha256": validation.sha256,
        "files": [entry.__dict__ for entry in validation.files],
        "scripts": validation.scripts,
        "compatibility": validation.compatibility,
    }


@router.post("/skills/{slug}/versions/{version}/publish")
async def publish_version(slug: str, version: str, request: Request):
    try:
        publish_skill_version(
            request.app.state.database,
            slug=slug,
            version=version,
            actor=_actor(request),
        )
    except SkillDomainError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"slug": slug, "version": version, "status": "published"}


@router.put("/skills/{slug}/versions/{version}/package")
async def edit_draft_package(
    slug: str,
    version: str,
    request: Request,
    file: UploadFile = File(...),
    changelog: str | None = Form(None),
):
    content = await file.read()
    try:
        validation = replace_draft_package(
            request.app.state.database,
            slug=slug,
            version=version,
            content=content,
            changelog=changelog,
            actor=_actor(request),
        )
    except SkillDomainError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {
        "slug": slug,
        "version": version,
        "status": "draft",
        "sha256": validation.sha256,
        "files": [entry.__dict__ for entry in validation.files],
        "scripts": validation.scripts,
    }


@router.post("/skills/{slug}/deprecate")
async def deprecate_skill(slug: str, request: Request):
    try:
        change_skill_lifecycle(
            request.app.state.database,
            slug=slug,
            action="deprecate",
            actor=_actor(request),
        )
    except SkillDomainError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"slug": slug, "status": "deprecated"}


@router.post("/skills/{slug}/archive")
async def archive_skill(slug: str, request: Request):
    try:
        change_skill_lifecycle(
            request.app.state.database,
            slug=slug,
            action="archive",
            actor=_actor(request),
        )
    except SkillDomainError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"slug": slug, "status": "archived"}


@router.delete("/skills/{slug}")
async def delete_skill(slug: str, request: Request):
    try:
        delete_draft_skill(request.app.state.database, slug=slug, actor=_actor(request))
    except SkillDomainError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"slug": slug, "deleted": True}
