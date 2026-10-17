"""Sampling 人工输入容量、格式及并发问题隔离。"""

import asyncio
import json

import pytest
from mcp import types

from app.services.inspector_runs import InspectorRuns
from app.services.inspector_sampling import MAX_BYTES, sampling_answer, sampling_question


def parameters(text: str = "fixture") -> types.CreateMessageRequestParams:
    """构造不触发模型调用的协议输入。"""
    return types.CreateMessageRequestParams(max_tokens=10, messages=[
        types.SamplingMessage(role="user", content=types.TextContent(type="text", text=text))
    ])


def test_oversize_request_and_response_rejected() -> None:
    """多字节输入以 UTF-8 字节限制，而不是字符数限制。"""
    with pytest.raises(ValueError, match="64 KiB"):
        sampling_question(parameters("中" * (MAX_BYTES // 2)))
    question = sampling_question(parameters())
    with pytest.raises(ValueError, match="限制"):
        sampling_answer(question, {"response_json": "中" * (MAX_BYTES // 2)})
    with pytest.raises(ValueError, match="有效"):
        sampling_answer(question, {"response_json": '{"role":"assistant"}'})


@pytest.mark.parametrize("kind,mime", [("image", "image/png"), ("audio", "audio/wav")])
@pytest.mark.parametrize("with_tools", [False, True])
def test_media_result_preserves_protocol_bytes(kind: str, mime: str, with_tools: bool) -> None:
    """媒体作为协议数据返回；该测试不声称图片或音频编码有效。"""
    question = sampling_question(parameters())
    question["with_tools"] = with_tools
    content = {"type": kind, "mimeType": mime, "data": "AP8="}
    payload = {"role": "assistant", "model": "manual-media",
               "content": [content] if with_tools else content}
    result = sampling_answer(question, {"response_json": json.dumps(payload)})
    assert result.model_dump(mode="json", by_alias=True, exclude_none=True) == payload


@pytest.mark.asyncio
async def test_concurrent_question_cannot_replace_original_waiter() -> None:
    """拒绝第二个问题不覆盖原 Future，首次回答后清理敏感上下文。"""
    runs = InspectorRuns()
    runs.active_id = "fixture"
    runs.runs["fixture"] = {"status": "working"}
    pending = asyncio.create_task(runs.sample([], parameters(), None))
    try:
        await asyncio.sleep(0)
        question = runs.snapshot("fixture")["question"]
        with pytest.raises(ValueError, match="待回答"):
            await runs.sample([], parameters("second"), None)
        assert runs.snapshot("fixture")["question"]["id"] == question["id"]
        runs.answer("fixture", question["id"], "accept", {"response_json": json.dumps({
            "role": "assistant", "model": "manual",
            "content": {"type": "text", "text": "approved"},
        })})
        assert (await pending).model == "manual"
        assert not runs.questions and "question" not in runs.snapshot("fixture")
        with pytest.raises(ValueError, match="失效"):
            runs.answer("fixture", question["id"], "accept", {})
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await runs.close()
