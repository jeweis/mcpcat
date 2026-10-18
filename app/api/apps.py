"""Apps 管理 API，不作为远程 UI 执行入口或旧 Skills API。"""

import mimetypes
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.middleware.auth import get_current_user
from app.models.app_bindings import AppBindingsRequest
from app.models.app_review import AppCSP, AppPermissions
from app.models.mcp_config import PermissionType
from app.services.app_audit import app_audit_actor
from app.services.app_connections import app_connections
from app.services.app_discovery import app_sources, discover_source_apps
from app.services.app_content_service import (
    AppDomainError,
    app_detail,
    list_apps,
    load_app_content,
    load_app_file,
)
from app.services.app_preview import preview_app
from app.services.app_preview_call import call_preview_tool
from app.services.app_preview_cancellation import run_preview_request
from app.services.app_publication_service import (
    configure_app_draft,
    publish_app_version,
    withdraw_app_version,
)
from app.services.app_registry_service import upload_app
from app.services.app_tool_bindings import (
    AppToolResolver,
    publication_validator,
    replace_app_bindings,
)
from app.services.security_service import security_service


def _enabled(response: Response) -> None:
    """管理能力固定提供；私有内容不允许公共缓存。"""
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"


router = APIRouter(prefix="/apps", dependencies=[Depends(_enabled)])


class DraftConfig(BaseModel):
    """配置为完整替换，客户端必须显式保留现有审核项。"""

    model_config = ConfigDict(extra="forbid")
    csp: AppCSP = Field(default_factory=AppCSP)
    permissions: AppPermissions = Field(default_factory=AppPermissions)
    changelog: str = Field(default="", max_length=4000)


class PublishRequest(BaseModel):
    """显式确认内容审核，公开范围为 App 下已发布版本。"""

    model_config = ConfigDict(extra="forbid")
    reviewed: bool = Field(default=False, strict=True)
    public: bool = Field(default=False, strict=True)


class PreviewCallRequest(BaseModel):
    """确认由父管理页面收集，不能用它替代正常的 Key/来源权限。"""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, object] = Field(default_factory=dict)
    confirmed: bool = Field(default=False, strict=True)


def _drafts(request: Request) -> bool:
    return (get_current_user(request) or {}).get("permission") == "write"


def _resolver(
    request: Request, permission: PermissionType = PermissionType.WRITE
) -> AppToolResolver:
    """不信任首次中间件快照，审核期间仍检查 Key 的有效性。"""

    def authorize() -> bool:
        key = security_service.verify_api_key(
            request.headers.get(security_service.get_auth_header_name(), "")
        )
        return bool(key and security_service.has_permission(key, permission))

    return AppToolResolver(
        request.app.state.database,
        request.app.state.server_manager,
        authorize,
        principal=lambda: app_audit_actor(
            request.app.state.database,
            request.headers.get(security_service.get_auth_header_name(), ""),
        ),
    )


async def _async_call(callback, *args, **kwargs):
    """来源目录查询运行于异步链，失败不给调用者原始上游异常。"""
    try:
        return await callback(*args, **kwargs)
    except AppDomainError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=409, detail="App 内容或来源暂不可用") from error


async def _call(callback, *args, **kwargs):
    """将校验和存储放到线程池，错误不回显宿主路径或 SQL。"""
    try:
        return await run_in_threadpool(callback, *args, **kwargs)
    except AppDomainError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(
            status_code=409, detail="App 制品暂不可读取或保存"
        ) from error
    except IntegrityError as error:
        raise HTTPException(
            status_code=409, detail="App 或版本已存在，请使用新版本"
        ) from error


@router.get("")
async def apps_list(request: Request) -> list[dict]:
    """管理列表不向 READ 暴露草稿最新版本。"""
    return await _call(
        list_apps, request.app.state.database, include_drafts=_drafts(request)
    )


@router.get("/discovery/sources")
async def apps_sources(request: Request) -> list[dict]:
    """来源索引不扫描上游，也不返回配置凭证。"""
    return app_sources(request)


@router.get("/discovery/sources/{server_name}")
async def apps_discover(server_name: str, request: Request) -> dict:
    """只读发现该来源应用，不执行入口或导入托管 Registry。"""
    return await discover_source_apps(request, server_name)


@router.get("/{slug}")
async def apps_detail(slug: str, request: Request) -> dict:
    """查看有权限的版本及审核配置，不返回制品磁盘路径。"""
    return await _call(
        app_detail,
        request.app.state.database,
        slug=slug,
        include_drafts=_drafts(request),
    )


@router.get("/{slug}/connections")
async def apps_connections(slug: str, request: Request) -> dict:
    """接入提示只读，不自动启用 Apps 或登记新的端点身份。"""
    detail = await apps_detail(slug, request)
    return await _async_call(app_connections, request, detail)


@router.post("/upload")
async def apps_upload(
    request: Request,
    slug: str = Form(...),
    display_name: str = Form(..., max_length=255),
    version: str = Form(...),
    entrypoint: str | None = Form(None),
    file: UploadFile = File(...),
) -> dict:
    """有界上传静态包，只创建草稿，不执行或自动发布。"""
    try:
        content = await file.read(settings.mcpcat_app_max_bytes + 1)
        await _call(
            upload_app,
            request.app.state.database,
            slug=slug,
            display_name=display_name,
            version=version,
            content=content,
            filename=file.filename or "app.zip",
            entrypoint=entrypoint,
            actor=(get_current_user(request) or {}).get("name"),
        )
        return await apps_detail(slug, request)
    finally:
        await file.close()


@router.put("/{slug}/versions/{version}/config")
async def apps_config(
    slug: str, version: str, body: DraftConfig, request: Request
) -> dict:
    """仅修改草稿审核项，已发布版本需创建新版本。"""
    await _call(
        configure_app_draft,
        request.app.state.database,
        slug=slug,
        version=version,
        **body.model_dump(exclude={"csp", "permissions"}),
        csp=body.csp,
        permissions=body.permissions,
    )
    return await apps_detail(slug, request)


@router.post("/{slug}/versions/{version}/publish")
async def apps_publish(
    slug: str, version: str, body: PublishRequest, request: Request
) -> dict:
    """明确审核后发布；公开选择作用于整个 App 的已发布版本。"""
    validator = await _async_call(
        publication_validator,
        request.app.state.database,
        _resolver(request),
        slug,
        version,
    )
    await _call(
        publish_app_version,
        request.app.state.database,
        slug=slug,
        version=version,
        validate_bindings=validator,
        **body.model_dump(),
    )
    return await apps_detail(slug, request)


@router.put("/{slug}/versions/{version}/bindings")
async def apps_bindings(
    slug: str, version: str, body: AppBindingsRequest, request: Request
) -> dict:
    """显式替换当前草稿允许的工具，不授权整个目录。"""
    await _async_call(
        replace_app_bindings,
        request.app.state.database,
        _resolver(request),
        slug=slug,
        version=version,
        bindings=body.bindings,
    )
    return await apps_detail(slug, request)


@router.post("/{slug}/versions/{version}/withdraw")
async def apps_withdraw(slug: str, version: str, request: Request) -> dict:
    """撤销不删除原包，也不让普通读取继续取得旧内容。"""
    await _call(
        withdraw_app_version, request.app.state.database, slug=slug, version=version
    )
    return await apps_detail(slug, request)


@router.get("/{slug}/versions/{version}/preview")
async def apps_preview(slug: str, version: str, request: Request) -> dict:
    """返回 JSON 预览数据，绝不将上传 HTML 作为同源页面响应。"""
    drafts = _drafts(request)
    return await _async_call(
        preview_app,
        request.app.state.database,
        _resolver(request, PermissionType.WRITE if drafts else PermissionType.READ),
        slug=slug,
        version=version,
        include_drafts=drafts,
    )


@router.post("/{slug}/versions/{version}/preview-call")
async def apps_preview_call(
    slug: str, version: str, body: PreviewCallRequest, request: Request
) -> dict:
    """真实工具测试沿用普通 MCP 的 READ 身份，不给予额外目录权限。"""
    return await _async_call(
        run_preview_request,
        request,
        lambda: call_preview_tool(
            request.app.state.database,
            _resolver(request, PermissionType.READ),
            slug=slug,
            version=version,
            **body.model_dump(),
        ),
    )


@router.get("/{slug}/versions/{version}/download")
async def apps_download(slug: str, version: str, request: Request) -> Response:
    """下载原始上传字节，HTML 不以内联页面执行。"""
    info, content = await _call(
        load_app_content,
        request.app.state.database,
        slug=slug,
        version=version,
        include_drafts=_drafts(request),
    )
    suffix = {
        "application/zip": "zip",
        "application/json": "json",
        "text/html": "html",
    }[info["media_type"]]
    return Response(
        content,
        media_type=info["media_type"],
        headers={
            "Content-Disposition": f'attachment; filename="{slug}-{version}.{suffix}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "X-Checksum-Sha256": info["sha256"],
            "ETag": f'"{info["sha256"]}"',
        },
    )


@router.get("/{slug}/versions/{version}/file")
async def apps_file(slug: str, version: str, path: str, request: Request) -> dict:
    """源码 JSON 预览；二进制与大文件保留可下载提示。"""
    data = await _call(
        load_app_file,
        request.app.state.database,
        slug=slug,
        version=version,
        path=path,
        include_drafts=_drafts(request),
    )
    reason, text = None, None
    if len(data) > 512 * 1024:
        reason = "too_large"
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            reason = "binary"
    return {
        "path": path,
        "size": len(data),
        "media_type": mimetypes.guess_type(path)[0] or "application/octet-stream",
        "is_markdown": PurePosixPath(path).suffix.lower() in {".md", ".markdown"},
        "previewable": reason is None,
        "reason": reason,
        "content": text,
    }
