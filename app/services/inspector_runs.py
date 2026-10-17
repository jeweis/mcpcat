"""有界的测试会话调用；协议任务与本地请求状态明确区分。"""

import asyncio
import json
import uuid
from time import monotonic
from typing import Any

import anyio
from fastmcp.client.elicitation import ElicitResult
from fastmcp_tasks import call_tool_task
from jsonschema import Draft202012Validator

from app.services.inspector_skills import capabilities
from app.services.inspector_questions import make_question
from app.services.inspector_sampling import sampling_question, sampling_answer
from app.services.inspector_task_inputs import resolve_task_inputs

MAX_RESULT_BYTES = 16 * 1024 * 1024
MAX_INPUT_BYTES = 64 * 1024
RUN_TIMEOUT = 300
_slots = asyncio.Semaphore(8)


class InspectorRuns:
    """每个 InspectorSession 独享；不接受客户端提供的上游 task ID。"""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.workers: dict[str, asyncio.Task] = {}
        self.handles: dict[str, Any] = {}
        self.cancel_signals: dict[str, asyncio.Event] = {}
        self.questions: dict[str, asyncio.Future] = {}
        self.active_id: str | None = None
        self.closed = False

    def start(
        self,
        session: Any,
        name: str,
        arguments: dict,
        tasked: bool,
        operation: str = "tools",
    ) -> dict:
        """只调度一次调用，容量在发送任何上游请求前检查。"""
        if self.closed or any(not worker.done() for worker in self.workers.values()):
            raise ValueError("当前会话已有运行中的调用，或会话已关闭")
        if operation not in {"tools", "resources", "templates", "prompts"}:
            raise ValueError("不支持的交互操作")
        if tasked and operation != "tools":
            raise ValueError("长任务只适用于工具调用")
        if len(json.dumps(arguments).encode()) > MAX_INPUT_BYTES:
            raise ValueError("调用参数超过 64 KiB 限制")
        while len(self.runs) >= 20:
            old = next(iter(self.runs))
            self.runs.pop(old)
            self.workers.pop(old, None)
        run_id = str(uuid.uuid4())
        run = {"id": run_id, "status": "queued", "kind": "task" if tasked else "call"}
        if operation != "tools":
            run["operation"] = operation
        self.runs[run_id] = run
        self.workers[run_id] = asyncio.create_task(
            self._execute(session, run, name, arguments, tasked)
        )
        return dict(run)

    def snapshot(self, run_id: str) -> dict:
        """返回会话自己的结果快照，不披露上游 task ID。"""
        if run_id not in self.runs:
            raise ValueError("调用不存在或已清理")
        return dict(self.runs[run_id])

    async def _execute(
        self, session: Any, run: dict, name: str, arguments: dict, tasked: bool
    ) -> None:
        started = monotonic()
        record = {"operation": "interactive_call", "status": "running"}
        session.records.append(record)
        del session.records[:-100]
        try:
            with anyio.fail_after(RUN_TIMEOUT):
                async with _slots, session.lock, session.client:
                    self.active_id = run["id"]
                    run["status"] = "working"
                    result = await self._invoke(
                        session.client, run, name, arguments, tasked
                    )
                    payload = (
                        result
                        if isinstance(result, dict)
                        else {
                            "content": [
                                b.model_dump(
                                    mode="json", by_alias=True, exclude_none=True
                                )
                                for b in result.content
                            ],
                            "structuredContent": result.structured_content,
                            "isError": result.is_error,
                        }
                    )
                    if len(json.dumps(payload).encode()) > MAX_RESULT_BYTES:
                        raise ValueError("响应超过测试台 16 MiB 限制")
                    run.update(
                        status=run.get("remote_status")
                        or ("failed" if payload.get("isError") else "completed"),
                        result=payload,
                    )
        except asyncio.CancelledError:
            run.update(status="unconfirmed", error="已停止等待；远端副作用不保证撤销")
        except TimeoutError:
            run.update(status="unconfirmed", error="调用超时，结果未确认；请勿自动重试")
        except Exception:
            run.update(
                status="failed",
                error="调用失败，请检查端点能力、权限及参数；不会自动重试",
            )
        finally:
            self.active_id = None
            self.handles.pop(run["id"], None)
            self.cancel_signals.pop(run["id"], None)
            pending = self.questions.pop(run["id"], None)
            if pending is not None and not pending.done():
                pending.cancel()
            run.pop("question", None)
            run["duration_ms"] = round((monotonic() - started) * 1000)
            record.update(status=run["status"], duration_ms=run["duration_ms"])

    async def _invoke(
        self, client: Any, run: dict, name: str, arguments: dict, tasked: bool
    ) -> Any:
        operation = run.get("operation", "tools")
        if operation != "tools":
            from app.services import inspector_workbench

            actions = {
                "resources": lambda: inspector_workbench.read_resource(client, name),
                "templates": lambda: inspector_workbench.read_template(
                    client, name, arguments
                ),
                "prompts": lambda: inspector_workbench.get_prompt(
                    client, name, arguments
                ),
            }
            return await actions[operation]()
        if not tasked:
            return await client.call_tool(
                name, arguments, timeout=RUN_TIMEOUT, raise_on_error=False
            )
        extensions = capabilities(client)["capabilities"].get("extensions", {})
        if "io.modelcontextprotocol/tasks" not in extensions:
            raise ValueError("端点未声明长任务扩展")
        handle = await call_tool_task(
            client, name, arguments, timeout=30, raise_on_error=False
        )
        self.handles[run["id"]] = handle
        signal = self.cancel_signals[run["id"]] = asyncio.Event()
        # 公开句柄负责任务结果，SDK 公开分发覆盖全部输入类型。
        result = await resolve_task_inputs(client, handle, signal)
        final = await handle.status()
        run["remote_status"] = final.status
        return result

    async def elicit(
        self, message: str, response_type: Any, params: Any, context: Any
    ) -> ElicitResult:
        """向当前用户请求表单或 URL 交互，不自动打开或获取 URL。"""
        run_id = self.active_id
        if run_id is None:
            return ElicitResult(action="cancel")
        question = make_question(message, params)
        if question is None:
            return ElicitResult(action="cancel")
        return await self._wait_answer(run_id, question)

    async def sample(self, messages: Any, params: Any, context: Any) -> Any:
        """显式模拟采样，必须属于当前调用且由用户逐次回答。"""
        if self.active_id is None or self.closed:
            raise ValueError("Sampling 不属于可交互的测试调用")
        return await self._wait_answer(self.active_id, sampling_question(params))

    async def _wait_answer(self, run_id: str, question: dict) -> Any:
        if run_id in self.questions:
            raise ValueError("当前调用已有待回答的问题")
        future = asyncio.get_running_loop().create_future()
        self.questions[run_id] = future
        self.runs[run_id].update(status="input_required", question=question)
        try:
            return await future
        finally:
            self.questions.pop(run_id, None)
            self.runs[run_id].pop("question", None)
            self.runs[run_id]["status"] = "working"

    def answer(
        self, run_id: str, question_id: str, action: str, content: dict | None
    ) -> None:
        """按本轮问题校验回答，拒绝过期、重复及不符合 Schema 的输入。"""
        run = self.snapshot(run_id)
        question = run.get("question", {})
        future = self.questions.get(run_id)
        if question.get("id") != question_id or future is None or future.done():
            raise ValueError("问题已失效，请刷新状态")
        if action not in {"accept", "decline", "cancel"}:
            raise ValueError("无效回答动作")
        if question.get("mode") == "sampling":
            if action == "accept":
                future.set_result(sampling_answer(question, content))
            else:
                future.set_exception(ValueError("用户拒绝或取消模拟 Sampling"))
            return
        if question.get("mode") == "url":
            if content is not None:
                raise ValueError("URL 交互只接受确认，不接收网页中的敏感信息")
        elif action == "accept":
            if len(json.dumps(content).encode()) > MAX_INPUT_BYTES:
                raise ValueError("回答超过 64 KiB 限制")
            validator = Draft202012Validator(question["schema"])
            if not validator.is_valid(content):
                raise ValueError("回答不符合请求的字段类型或必填要求")
        future.set_result(
            ElicitResult(action=action, content=content if action == "accept" else None)
        )

    async def cancel(self, run_id: str) -> dict:
        """合作式取消协议任务；普通调用仅停止本地等待，不承诺回滚。"""
        run = self.snapshot(run_id)
        worker = self.workers[run_id]
        if worker.done():
            return run
        handle = self.handles.get(run_id)
        if handle is not None:
            with anyio.fail_after(10):
                await handle.cancel()
            self.runs[run_id]["cancel_requested"] = True
            signal = self.cancel_signals.get(run_id)
            if signal is not None:
                signal.set()
        else:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            if self.runs[run_id]["status"] == "queued":
                self.runs[run_id].update(status="cancelled")
        return self.snapshot(run_id)

    async def close(self) -> None:
        """关闭所有本地等待并释放输入 Future；不假称远端撤销成功。"""
        self.closed = True
        for worker in self.workers.values():
            if not worker.done():
                worker.cancel()
        await asyncio.gather(*self.workers.values(), return_exceptions=True)
        self.questions.clear()
        self.handles.clear()
        self.cancel_signals.clear()
        self.runs.clear()
        self.workers.clear()
