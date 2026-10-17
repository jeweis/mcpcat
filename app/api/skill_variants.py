"""双分发管理接口，与旧 Registry v1 JSON 响应分离。"""

import mimetypes
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.api.skills import _actor, _include_drafts
from app.services.config_service import ConfigService
from app.services.security_service import security_service
from app.services.skill_content_service import clone_uploaded_for_remote, load_variant
from app.services.skill_profile_upload import upload_skill_profiles
from app.services.skill_variant_artifacts import file_media_type, read_manifest_file
from app.services.skill_variant_publication import (
    publish_content_version,
    withdraw_remote_variant,
)
from app.storage.runtime_identities import endpoint_identity
from app.storage.skill_repositories import SkillDomainError
from app.storage.unit_of_work import UnitOfWork

router = APIRouter(prefix="/skill-variants")


class ShareRemoteRequest(BaseModel):
    """从冻结上传内容创建新版本，明确选择端点适用范围。"""

    source_version: str
    new_version: str
    binding_kind: Literal["independent", "service", "catalog"] = "independent"
    source_name: str = Field(default="", max_length=64)


class PublishRequest(BaseModel):
    """匿名远程提供必须在发布审核时显式选择，默认不公开。"""

    remote_public: bool = False


class WithdrawRequest(BaseModel):
    binding: str = Field(default="", max_length=255)


def _binding(request: Request, kind: str, source: str) -> tuple[str, dict]:
    database = request.app.state.database
    manager = request.app.state.server_manager
    if kind == "independent":
        return "", {"binding_kind": "independent"}
    if kind == "service":
        info = manager.server_info.get(source)
        if not info or info.get("status") != "running":
            raise SkillDomainError("绑定的来源 MCP 服务未运行")
        return endpoint_identity(database, "service", source), {
            "binding_kind": kind,
            "source_name": source,
        }
    if kind == "catalog" and manager.catalog_service is not None:
        return endpoint_identity(database, "catalog"), {"binding_kind": kind}
    raise SkillDomainError("绑定端点不存在或未开启")


@router.get("")
async def list_variant_skills(request: Request):
    """新的管理列表包含 remote-only；旧列表仅列 local。"""
    with UnitOfWork(request.app.state.database) as unit:
        rows = unit.skills.list_visible(include_drafts=_include_drafts(request))
        results = []
        for row in rows:
            versions = unit.skill_versions.list_for_skill(row.id)
            published = next(
                (
                    item.version
                    for item in versions
                    if item.status in {"published", "deprecated"}
                ),
                None,
            )
            results.append(
                {
                    "slug": row.slug,
                    "display_name": row.display_name,
                    "description": row.description,
                    "source_type": row.source_type,
                    "source": row.source_ref_json,
                    "status": row.status,
                    "latest_version": (
                        row.latest_version if _include_drafts(request) else published
                    ),
                    "latest_published_version": published,
                    "updated_at": row.updated_at.isoformat(),
                }
            )
        return results


@router.get("/{slug}")
async def list_content_variants(slug: str, request: Request):
    """版本下呈现明确 profile/binding，不向旧严格 parser 塞字段。"""
    with UnitOfWork(request.app.state.database) as unit:
        skill = unit.skills.get_by_slug(slug)
        if skill is None or (
            not _include_drafts(request)
            and (skill.visibility != "published" or skill.status == "archived")
        ):
            raise HTTPException(status_code=404, detail="Skill 不存在")
        results = []
        for version in unit.skill_versions.list_for_skill(skill.id):
            if not _include_drafts(request) and version.status not in {
                "published",
                "deprecated",
            }:
                continue
            results.append(
                {
                    "version": version.version,
                    "status": version.status,
                    "changelog": version.changelog,
                    "compatibility": version.compatibility_json,
                    "generator_version": version.generator_version,
                    "tool_schema_hash": version.tool_schema_hash,
                    "created_at": version.created_at.isoformat(),
                    "published_at": (
                        version.published_at.isoformat()
                        if version.published_at
                        else None
                    ),
                    "variants": [
                        {
                            "profile": row.profile,
                            "binding": row.binding,
                            "status": row.status,
                            "frontmatter": row.frontmatter_json,
                            "manifest": row.manifest_json,
                            "metadata": row.metadata_json,
                        }
                        for row in unit.skill_variants.list_for_version(version.id)
                        if _include_drafts(request)
                        or row.status in {"published", "deprecated"}
                    ],
                }
            )
        return {
            "slug": slug,
            "display_name": skill.display_name,
            "description": skill.description,
            "source_type": skill.source_type,
            "source": skill.source_ref_json,
            "status": skill.status,
            "versions": results,
        }


@router.get("/{slug}/versions/{version}/file")
async def preview_variant_file(
    slug: str,
    version: str,
    request: Request,
    path: str,
    profile: Literal["local", "remote"] = "local",
    binding: str = "",
):
    try:
        info, content = load_variant(
            request.app.state.database,
            slug=slug,
            version=version,
            profile=profile,
            binding=binding,
            include_drafts=_include_drafts(request),
        )
        entry = next(
            (item for item in info["manifest"]["files"] if item["path"] == path), None
        )
        if entry is None:
            raise SkillDomainError("文件不在版本清单中")
        payload = {
            "path": path,
            "size": entry["size"],
            "is_markdown": Path(path).suffix.lower() in {".md", ".markdown"},
        }
        if entry["size"] > 512 * 1024:
            return {
                **payload,
                "media_type": mimetypes.guess_type(path)[0]
                or "application/octet-stream",
                "previewable": False,
                "reason": "too_large",
                "content": None,
            }
        data = read_manifest_file(content, info["manifest"], path)
        try:
            value = data.decode("utf-8")
        except UnicodeDecodeError:
            return {
                **payload,
                "media_type": file_media_type(path, data),
                "previewable": False,
                "reason": "binary",
                "content": None,
            }
        return {
            **payload,
            "media_type": file_media_type(path, data),
            "previewable": True,
            "content": value,
            "reason": None,
        }
    except SkillDomainError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/{slug}/versions/{version}/download")
async def download_variant(
    slug: str,
    version: str,
    request: Request,
    profile: Literal["local", "remote"] = "local",
    binding: str = "",
):
    try:
        info, content = load_variant(
            request.app.state.database,
            slug=slug,
            version=version,
            profile=profile,
            binding=binding,
            include_drafts=_include_drafts(request),
        )
        return Response(
            content,
            media_type="application/zip",
            headers={
                "ETag": f'"{info["sha256"]}"',
                "X-Checksum-Sha256": info["sha256"],
                "Content-Disposition": f'attachment; filename="{slug}-{version}-{profile}.zip"',
            },
        )
    except SkillDomainError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/{slug}/connections")
async def remote_connections(slug: str, request: Request):
    """只对实际注册且当前可用的端点给可复制地址，不把配置当运行证据。"""
    current = await list_content_variants(slug, request)
    scopes = getattr(request.app.state, "skills_mcp_scopes", {})
    cfg = ConfigService.get_setting_section("skills_mcp", {}) or {}
    base = ConfigService.get_public_base_url() or str(request.base_url).rstrip("/")
    connections = []
    for version in current["versions"]:
        for variant in version["variants"]:
            if variant["profile"] != "remote":
                continue
            scope = scopes.get(variant["binding"])
            ready = bool(
                scope
                and variant["status"] in {"published", "deprecated"}
                and version["status"] in {"published", "deprecated"}
            )
            metadata = variant["metadata"] or {}
            if not variant["binding"]:
                runtime = getattr(request.app.state, "skills_mcp_runtime", None)
                ready = ready and runtime is not None and runtime.available()
            else:
                runtime = getattr(request.app.state, "skills_mcp_runtime", None)
                ready = (
                    ready
                    and runtime is not None
                    and runtime.source_available(variant["binding"])
                )
                if ready:
                    ready = await runtime.attachments[
                        variant["binding"]
                    ].metadata_eligible(metadata)
            if metadata.get("binding_kind") == "service":
                info = request.app.state.server_manager.server_info.get(
                    metadata.get("source_name"), {}
                )
                ready = ready and info.get("status") == "running"
            if metadata.get("binding_kind") == "catalog":
                ready = (
                    ready
                    and request.app.state.server_manager.catalog_service is not None
                )
            if ready:
                try:
                    load_variant(
                        request.app.state.database,
                        slug=slug,
                        version=version["version"],
                        profile="remote",
                        binding=variant["binding"],
                    )
                except (ValueError, OSError):
                    ready = False
            connections.append(
                {
                    "version": version["version"],
                    "binding": variant["binding"],
                    "ready": ready,
                    "endpoint_url": (
                        f'{base}/mcp/{scope["path_name"]}' if ready else None
                    ),
                    "require_auth": (
                        cfg.get("require_auth", True)
                        if not variant["binding"]
                        else scope.get("require_auth", True) if scope else True
                    ),
                    "auth_header_name": security_service.get_auth_header_name(),
                    "public_readable": metadata.get("public_remote") is True,
                    "capability": "io.modelcontextprotocol/skills",
                    "reason": None if ready else "remote_serving_not_ready",
                    "client_note": "客户端需支持远程 Skills；否则使用 ZIP/CLI 本地安装。",
                }
            )
    return {"slug": slug, "connections": connections}


@router.post("/{slug}/share-remote")
async def share_uploaded_content(slug: str, body: ShareRemoteRequest, request: Request):
    try:
        binding, metadata = _binding(request, body.binding_kind, body.source_name)
        version_id = clone_uploaded_for_remote(
            request.app.state.database,
            slug=slug,
            source_version=body.source_version,
            new_version=body.new_version,
            binding=binding,
            actor=_actor(request),
            metadata=metadata,
        )
        return {
            "slug": slug,
            "version": body.new_version,
            "version_id": version_id,
            "status": "draft",
        }
    except SkillDomainError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/upload")
async def upload_profiles(
    request: Request,
    file: UploadFile = File(...),
    version: str = Form(...),
    selection: Literal["local", "remote", "both"] = Form("local"),
    binding_kind: Literal["independent", "service", "catalog"] = Form("independent"),
    source_name: str = Form(""),
    changelog: str = Form(""),
):
    from app.core.config import settings

    try:
        binding, metadata = _binding(request, binding_kind, source_name)
        content = await file.read(settings.mcpcat_skill_zip_max_bytes + 1)
        slug, version_id = await run_in_threadpool(
            upload_skill_profiles,
            request.app.state.database,
            content=content,
            version=version,
            selection=selection,
            changelog=changelog,
            binding=binding,
            metadata=metadata,
            actor=_actor(request),
        )
        info, _ = await run_in_threadpool(
            load_variant,
            request.app.state.database,
            slug=slug,
            version=version,
            profile="remote" if selection == "remote" else "local",
            binding=binding if selection == "remote" else "",
            include_drafts=True,
        )
        with UnitOfWork(request.app.state.database) as unit:
            record = unit.skill_versions.get_by_id(version_id)
            scripts = (record.source_snapshot_json or {}).get("scripts", [])
        return {
            "slug": slug,
            "version": version,
            "version_id": version_id,
            "status": "draft",
            "sha256": info["sha256"],
            "files": info["manifest"]["files"],
            "scripts": scripts,
        }
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/bindings/available")
async def available_upload_bindings(request: Request) -> list[dict]:
    """提供可审核绑定选项，查询不创建身份或暴露服务配置/凭证。"""
    manager = request.app.state.server_manager
    options = [{"kind": "independent", "source_name": "", "label": "独立 Skills 端点"}]
    options.extend(
        {"kind": "service", "source_name": name, "label": name}
        for name, info in sorted(manager.server_info.items())
        if info.get("status") == "running"
    )
    if manager.catalog_service is not None:
        options.append(
            {"kind": "catalog", "source_name": "", "label": "工具搜索 Catalog"}
        )
    return options


@router.post("/{slug}/versions/{version}/publish")
async def publish_variants(
    slug: str,
    version: str,
    request: Request,
    body: PublishRequest = Body(default_factory=PublishRequest),
):
    try:
        publish_content_version(
            request.app.state.database,
            slug=slug,
            version=version,
            actor=_actor(request),
            remote_public=body.remote_public,
        )
        return {"slug": slug, "version": version, "status": "published"}
    except SkillDomainError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/{slug}/versions/{version}/withdraw-remote")
async def withdraw_remote(
    slug: str,
    version: str,
    request: Request,
    body: WithdrawRequest = Body(default_factory=WithdrawRequest),
):
    try:
        withdraw_remote_variant(
            request.app.state.database,
            slug=slug,
            version=version,
            binding=body.binding,
            actor=_actor(request),
        )
        return {"slug": slug, "version": version, "remote_status": "archived"}
    except SkillDomainError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
