"""手工 Sampling 的实际 SDK 往返，不使用外部模型或生产凭证。"""

import asyncio
import json

import pytest
from fastmcp import Client, Context, FastMCP
from mcp import types
from test_inspector_events import wait_for
from test_sampling_modern_roundtrip import sampling_source

from app.services.inspector_service import InspectorSession
from app.services.inspector_runs import InspectorRuns


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
async def test_manual_sampling_roundtrip(mode: str, action: str) -> None:
    """两代协议都等待人工响应，拒绝/取消不返回伪造模型答案。"""
    source = sampling_source([]) if mode == "auto" else FastMCP("legacy sample")
    if mode == "legacy":
        @source.tool()
        async def sample(ctx: Context) -> dict:
            result = await ctx.session.create_message(
                messages=[types.SamplingMessage(role="user", content=types.TextContent(
                    type="text", text="fixture"))], max_tokens=10,
            )
            return result.model_dump(by_alias=True)

    runs = InspectorRuns()
    client = Client(source, mode=mode, sampling_handler=runs.sample)
    session = InspectorSession("fixture", client)
    session.runs = runs
    run = runs.start(session, "sample", {}, False)
    try:
        await wait_for(lambda: runs.snapshot(run["id"])["status"] == "input_required")
        question = runs.snapshot(run["id"])["question"]
        assert question["mode"] == "sampling" and "fixture" in question["message"]
        assert not runs.workers[run["id"]].done()
        with pytest.raises(ValueError):
            runs.answer(run["id"], question["id"], "accept", {"response_json": "bad"})
        content = {"response_json": json.dumps({
            "role": "assistant", "model": "manual-test",
            "content": {"type": "text", "text": "approved"},
        })} if action == "accept" else None
        runs.answer(run["id"], question["id"], action, content)
        async with asyncio.timeout(5):
            await runs.workers[run["id"]]
        final = runs.snapshot(run["id"])
        assert final["status"] == ("completed" if action == "accept" else "failed")
        if action == "accept":
            assert "manual-test" in str(final["result"])
        assert "question" not in final
        assert "approved" not in str(session.records)
    finally:
        await runs.close()
