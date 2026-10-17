"""通过 SDK 公开输入分发补齐任务句柄的 Sampling/Roots 回调。"""

import asyncio
import json
from typing import Any

from fastmcp_tasks.client_models import UpdateTaskRequest, UpdateTaskRequestParams
from mcp import types
from mcp.client.session import ClientRequestContext
from pydantic import TypeAdapter

_input_request = TypeAdapter(types.InputRequest)
_TERMINAL = {"completed", "failed", "cancelled"}
_MAX_BYTES = 64 * 1024


async def resolve_task_inputs(client: Any, handle: Any, cancelled: asyncio.Event) -> Any:
    """有界驱动输入；外层 InspectorRuns 负责总期限、关闭及身份隔离。"""
    rounds = 0
    delay = 0.02
    while True:
        current = await handle.status()
        if current.status in _TERMINAL:
            return await handle.result()
        if current.status == "input_required" and not cancelled.is_set():
            rounds += 1
            if rounds > 32:
                raise ValueError("任务交互超过轮次限制")
            await interruptible_answers(client, handle.task_id, current.input_requests, cancelled)
            delay = 0.02
            continue
        ceiling = current.poll_interval_ms
        ceiling = max(0.02, ceiling / 1000) if ceiling and ceiling > 0 else 1
        await asyncio.sleep(min(delay, ceiling))
        delay = min(delay * 2, ceiling)


async def interruptible_answers(
    client: Any, task_id: str, requests: Any, cancelled: asyncio.Event,
) -> None:
    """取消确认后结束本地问题等待，但仍由任务状态决定是否已取消。"""
    answer = asyncio.create_task(answer_task_inputs(client, task_id, requests))
    stopped = asyncio.create_task(cancelled.wait())
    try:
        done, _ = await asyncio.wait({answer, stopped}, return_when=asyncio.FIRST_COMPLETED)
        if answer in done:
            await answer
    finally:
        for task in (answer, stopped):
            if not task.done():
                task.cancel()
        await asyncio.gather(answer, stopped, return_exceptions=True)


async def answer_task_inputs(client: Any, task_id: str, requests: Any) -> None:
    """先校验整轮输入，再调用会话自己的回调并提交一次回答。"""
    if not isinstance(requests, dict) or not 1 <= len(requests) <= 16:
        raise ValueError("任务输入数量无效")
    if len(json.dumps(requests).encode()) > _MAX_BYTES:
        raise ValueError("任务输入超过 64 KiB 限制")
    parsed = {key: _input_request.validate_python(value) for key, value in requests.items()}
    responses = {}
    for key, request in parsed.items():
        context = ClientRequestContext(session=client.session, request_id=f"task-{key}")
        response = await client.session.dispatch_input_request(context, request)
        responses[key] = response.model_dump(mode="json", by_alias=True, exclude_none=True)
        if len(json.dumps(responses).encode()) > _MAX_BYTES:
            raise ValueError("任务回答超过 64 KiB 限制")
    await client.session.send_request(
        UpdateTaskRequest(params=UpdateTaskRequestParams(
            task_id=task_id, input_responses=responses,
        )), types.Result, request_read_timeout_seconds=30,
    )
