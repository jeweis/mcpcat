import asyncio
from time import monotonic
from typing import Annotated, Any, Dict, Literal

import anyio
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.services import inspector_skills, inspector_workbench
from app.services.inspector_service import InspectorSession, inspector_service
from app.services.security_service import security_service
from app.services.inspector_auth import auth_snapshot, diagnose_upstream
from app.services.inspector_client_options import InspectorClientOptions

router = APIRouter()
_operations = asyncio.Semaphore(8)


@router.get("/auth/{server_name}")
async def auth_diagnostic(server_name: str) -> dict:
    """连接失败前也可查看诊断；中间件已校验当前 READ 身份。"""
    from app.services.server_manager import server_manager

    info = server_manager.server_info.get(server_name)
    return {"gateway": "authenticated", "upstream": auth_snapshot(info)}


@router.post("/auth/{server_name}/probe")
async def probe_authorization(server_name: str) -> dict:
    """显式只读探测，不触发授权、注册、刷新或配置写入。"""
    from copy import deepcopy
    from app.services.server_manager import server_manager

    info = server_manager.server_info.get(server_name)
    if info is None:
        raise HTTPException(404, "该端点没有独立的上游授权配置")
    before = deepcopy(info.get("config"))
    result = await diagnose_upstream(info)
    if server_manager.server_info.get(server_name) is not info or info.get("config") != before:
        raise HTTPException(409, "探测期间来源配置已变化，请重新检查")
    return {"gateway": "authenticated", "upstream": result}


class CreateSessionRequest(BaseModel):
    server_name: str = Field(min_length=1, max_length=256)
    client_options: InspectorClientOptions | None = None


class CallToolRequest(BaseModel):
    tool: str = Field(min_length=1, max_length=4096)
    arguments: Dict[str, Any] = Field(default_factory=dict, max_length=128)


class RunRequest(CallToolRequest):
    """新测试台的有界调用；不改变旧同步调用契约。"""

    tasked: bool = False
    operation: Literal["tools", "resources", "templates", "prompts"] = "tools"


class CompletionRequest(BaseModel):
    """有界参数补全输入；引用是 MCP 标识，不作为 HTTP 地址请求。"""

    kind: Literal["prompts", "templates"]
    reference: str = Field(min_length=1, max_length=4096)
    argument: str = Field(min_length=1, max_length=256)
    value: str = Field(default="", max_length=4096)
    context: dict[
        Annotated[str, Field(min_length=1, max_length=256)],
        Annotated[str, Field(max_length=4096)],
    ] = Field(default_factory=dict, max_length=32)


class SubscriptionRequest(BaseModel):
    """有界订阅过滤器，不接受任意目标服务器。"""

    tools_list_changed: bool = True
    prompts_list_changed: bool = True
    resources_list_changed: bool = True
    resource_subscriptions: list[Annotated[str, Field(min_length=1, max_length=4096)]] = Field(
        default_factory=list, max_length=32,
    )


@router.get("/sessions/{session_id}/events")
async def get_events(
    session_id: str, request: Request, after: int = Query(default=0, ge=0),
) -> dict:
    """读取当前身份的有界真实事件，不触发新的协议操作。"""
    return owned_session(session_id, request).events.snapshot(after)


@router.post("/sessions/{session_id}/subscription")
async def start_subscription(
    session_id: str, req: SubscriptionRequest, request: Request,
) -> dict:
    """独立连接监听，不占用普通操作的会话锁。"""
    session = owned_session(session_id, request)
    try:
        return session.events.start(
            session.client, req.model_dump(), session.subscription_authorized,
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.delete("/sessions/{session_id}/subscription")
async def stop_subscription(session_id: str, request: Request) -> dict:
    """显式停止当前会话的监听。"""
    return await owned_session(session_id, request).events.stop()


@router.post("/sessions/{session_id}/complete")
async def complete_argument(
    session_id: str, req: CompletionRequest, request: Request,
) -> dict:
    """沿用会话所有权、并发及超时控制执行只读补全。"""
    return await skill_operation(
        owned_session(session_id, request), inspector_workbench.complete_argument,
        req.kind, req.reference, req.argument, req.value, req.context,
    )


class RunAnswer(BaseModel):
    """只回答当前会话待处理的问题，不允许指定任意上游请求。"""

    question_id: str = Field(min_length=1, max_length=128)
    action: Literal["accept", "decline", "cancel"]
    content: dict[str, Any] | None = None


@router.post("/sessions/{session_id}/runs")
async def start_run(session_id: str, req: RunRequest, request: Request) -> dict:
    """异步启动真实协议操作，保留旧工具请求并支持读取期间的客户端交互。"""
    session = owned_session(session_id, request)
    try:
        return session.runs.start(
            session, req.tool, req.arguments, req.tasked, req.operation,
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/sessions/{session_id}/runs/{run_id}")
async def get_run(session_id: str, run_id: str, request: Request) -> dict:
    """按当前身份读取运行状态和最终结果。"""
    session = owned_session(session_id, request)
    try:
        return session.runs.snapshot(run_id)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error


@router.post("/sessions/{session_id}/runs/{run_id}/answer")
async def answer_run(
    session_id: str, run_id: str, req: RunAnswer, request: Request
) -> dict:
    """显式回答、拒绝或取消本轮表单。"""
    session = owned_session(session_id, request)
    try:
        session.runs.answer(run_id, req.question_id, req.action, req.content)
        return {"status": "submitted"}
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/sessions/{session_id}/runs/{run_id}/cancel")
async def cancel_run(session_id: str, run_id: str, request: Request) -> dict:
    """请求取消，不保证已发生的副作用撤销。"""
    session = owned_session(session_id, request)
    try:
        return await session.runs.cancel(run_id)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error
    except Exception as error:
        raise HTTPException(502, "取消未确认，请继续查询状态") from error


def owned_session(session_id: str, request: Request) -> InspectorSession:
    """每次操作校验当前 HTTP 身份，不向其他身份暴露会话存在性。"""
    try:
        return inspector_service.authorize(
            session_id, request.headers.get(security_service.get_auth_header_name())
        )
    except Exception as error:
        raise HTTPException(404, "会话不存在或已过期") from error


class SkillRequest(BaseModel):
    """仅接收协议 URI，不接受浏览器提供的校验元数据。"""

    uri: str = Field(min_length=1, max_length=4096)
    skill_uri: str | None = Field(default=None, max_length=4096)
    mode: Literal["official", "provider", "auto"] = "official"
    cursor: str | None = Field(default=None, max_length=8192)


async def skill_operation(session: InspectorSession, action: Any, *args: Any) -> Any:
    """限制等待与并发，并避免原始协议异常泄漏认证信息。"""
    started = monotonic()
    record = {"operation": action.__name__, "status": "running"}
    session.records.append(record)
    del session.records[:-100]
    try:
        with anyio.fail_after(25):
            async with _operations, session.lock, session.client:
                result = await action(session.client, *args)
                record["status"] = "completed"
                return result
    except TimeoutError as error:
        record["status"] = "unconfirmed"
        raise HTTPException(
            504, "请求超时，结果未确认；调用可能已产生副作用，请勿自动重试"
        ) from error
    except ValueError as error:
        record["status"] = "invalid"
        raise HTTPException(422, str(error)) from error
    except Exception as error:
        record["status"] = "failed"
        raise HTTPException(
            502, "MCP 请求失败，请确认资源权限、端点状态和协议支持"
        ) from error
    finally:
        record["duration_ms"] = round((monotonic() - started) * 1000)
        if record["status"] == "running":
            record["status"] = "unconfirmed"


@router.get("/sessions/{session_id}/capabilities")
async def get_capabilities(session_id: str, request: Request):
    """返回实际握手信息，不将管理配置作为能力证明。"""

    async def read(client):
        return inspector_skills.capabilities(client)

    return await skill_operation(owned_session(session_id, request), read)


@router.post("/sessions/{session_id}/skills/directory")
async def read_directory(session_id: str, req: SkillRequest, request: Request):
    """可选目录测试；未协商能力时不会发送方法。"""
    return await skill_operation(
        owned_session(session_id, request),
        inspector_skills.read_directory,
        req.skill_uri or req.uri,
        req.uri,
        req.cursor,
    )


@router.get("/sessions/{session_id}/skills")
async def list_skills(
    session_id: str,
    request: Request,
    mode: Literal["official", "provider", "auto"] = "official",
    cursor: str | None = None,
):
    """分页发现，不隐式切换 Provider 模式。"""
    session = owned_session(session_id, request)
    result = await skill_operation(session, inspector_skills.list_skills, mode, cursor)
    for entry in result["skills"]:
        session.entries[(result["mode"], entry["uri"])] = entry
    while len(session.entries) > 512:
        del session.entries[next(iter(session.entries))]
    return result


@router.post("/sessions/{session_id}/skills/get")
async def get_skill(session_id: str, req: SkillRequest, request: Request):
    """支持列表之外的直接 URI。"""
    session = owned_session(session_id, request)
    result = await skill_operation(
        session,
        inspector_skills.get_skill,
        req.uri,
        req.mode,
    )
    session.entries[(result["mode"], req.uri)] = result["skill"]
    while len(session.entries) > 512:
        del session.entries[next(iter(session.entries))]
    return result


@router.post("/sessions/{session_id}/skills/read")
async def read_skill(session_id: str, req: SkillRequest, request: Request):
    """按需读取并核验，不运行任何上传内容。"""
    session = owned_session(session_id, request)
    entry = session.entries.get((req.mode, req.skill_uri or req.uri))
    if entry is None:
        raise HTTPException(409, "请先发现或查询 Skill 元数据")
    return await skill_operation(
        session,
        inspector_skills.read_file,
        req.skill_uri or req.uri,
        req.uri,
        req.mode,
        entry,
    )


@router.post("/sessions")
async def create_session(req: CreateSessionRequest, request: Request):
    # Determine base URL for internal connection
    # Prefer localhost to avoid external network issues
    port = request.app.state.port if hasattr(request.app.state, "port") else 8000
    base_url = f"http://127.0.0.1:{port}"

    # Get API key from current request to proxy authentication
    api_key = request.headers.get(security_service.get_auth_header_name())

    try:
        session_id = await inspector_service.create_session(
            req.server_name, base_url, api_key=api_key,
            **({"client_options": req.client_options} if req.client_options else {}),
        )
        return {"session_id": session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/tools")
async def get_tools(session_id: str, request: Request):
    async def tools(client):
        supported = "tools" in inspector_skills.capabilities(client)["capabilities"]
        return {
            "tools": await inspector_service.get_tools(session_id),
            "supported": supported,
            "tasks_supported": "io.modelcontextprotocol/tasks"
            in inspector_skills.capabilities(client)["capabilities"].get(
                "extensions", {}
            ),
        }

    return await skill_operation(owned_session(session_id, request), tools)


@router.post("/sessions/{session_id}/call")
async def call_tool(session_id: str, req: CallToolRequest, request: Request):
    async def invoke(client):
        return await inspector_service.call_tool(session_id, req.tool, req.arguments)

    return await skill_operation(owned_session(session_id, request), invoke)


class InspectRequest(BaseModel):
    """有界的资源/提示/应用操作参数，不接受任意 URL 连接。"""

    name: str = Field(min_length=1, max_length=4096)
    arguments: dict[str, str] = Field(default_factory=dict, max_length=128)


class AppCallRequest(CallToolRequest):
    """应用入口与当前要调用的工具分别验证。"""

    entry: str = Field(min_length=1, max_length=4096)


@router.get("/sessions/{session_id}/records")
async def records(session_id: str, request: Request):
    """只返回当前身份的有界操作摘要，不包含参数和凭证。"""
    return {"records": owned_session(session_id, request).records}


@router.get("/sessions/{session_id}/browse/{kind}")
async def browse(
    session_id: str,
    kind: Literal["resources", "templates", "prompts", "apps"],
    request: Request,
    cursor: str | None = None,
):
    """按需分页发现当前端点能力。"""
    return await skill_operation(
        owned_session(session_id, request), inspector_workbench.listing, kind, cursor
    )


@router.post("/sessions/{session_id}/inspect/{kind}")
async def inspect_content(
    session_id: str,
    kind: Literal["resources", "templates", "prompts", "apps"],
    req: InspectRequest,
    request: Request,
):
    """读取内容不隐式执行工具、HTML 或模型。"""
    operation = {
        "resources": inspector_workbench.read_resource,
        "templates": inspector_workbench.read_template,
        "prompts": inspector_workbench.get_prompt,
        "apps": inspector_workbench.app_preview,
    }[kind]
    args = (
        (req.name, req.arguments) if kind in {"templates", "prompts"} else (req.name,)
    )
    return await skill_operation(owned_session(session_id, request), operation, *args)


@router.post("/sessions/{session_id}/apps/call")
async def app_call(session_id: str, req: AppCallRequest, request: Request):
    """隔离应用经父页面确认后，使用当前身份真实调用。"""
    return await skill_operation(
        owned_session(session_id, request),
        inspector_workbench.app_call,
        req.entry,
        req.tool,
        req.arguments,
    )


@router.delete("/sessions/{session_id}")
async def close_session(session_id: str, request: Request):
    owned_session(session_id, request)
    await inspector_service.close_session(session_id)
    return {"status": "ok"}


@router.post("/sessions/{session_id}/apps/open")
async def app_open(session_id: str, req: CallToolRequest, request: Request):
    """主动调用入口可能产生副作用，不自动重试。"""
    return await skill_operation(
        owned_session(session_id, request),
        inspector_workbench.app_open,
        req.tool,
        req.arguments,
    )
