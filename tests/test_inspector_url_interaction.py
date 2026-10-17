"""真实 SDK 新旧 URL 交互及输入安全，不访问任何外部网页。"""

import asyncio
from types import SimpleNamespace

import pytest
from fastmcp import Client, Context, FastMCP
from mcp_types import ElicitRequest, ElicitRequestURLParams, InputRequiredResult

from app.services.inspector_questions import make_question
from app.services.inspector_runs import InspectorRuns
from app.services.inspector_service import InspectorSession


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "file:///etc/passwd", "//example.com",
    "https://user:secret@example.com", "https://example.com\\@evil.test",
    "https://example.com\n/path", "https://[broken", "https://example.com:invalid",
])
def test_reject_unsafe_navigation(url):
    assert make_question("open", SimpleNamespace(mode="url", url=url)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
async def test_url_real_sdk_resumes_only_after_current_user_answer(mode, action):
    server = FastMCP("url fixture")
    effects = []

    @server.tool()
    async def visit(ctx: Context) -> str | InputRequiredResult:
        if mode == "auto":
            if ctx.input_responses is None:
                return InputRequiredResult(input_requests={"visit": ElicitRequest(
                    params=ElicitRequestURLParams(
                        message="请在网页完成确认", url="https://example.test/confirm",
                    ),
                )})
            answer = ctx.input_responses["visit"]
        else:
            answer = await ctx.session.elicit_url(
                "请在网页完成确认", "https://example.test/confirm", "fixture-visit",
                related_request_id=ctx.request_id,
            )
        if answer.action == "accept":
            effects.append("confirmed")
        return answer.action

    runs = InspectorRuns()
    client = Client(server, mode=mode, elicitation_handler=runs.elicit)
    session = InspectorSession("fixture", client)
    session.runs = runs
    run = runs.start(session, "visit", {}, False)
    try:
        async with asyncio.timeout(5):
            while runs.snapshot(run["id"])["status"] != "input_required":
                assert not runs.workers[run["id"]].done(), runs.snapshot(run["id"])
                await asyncio.sleep(0.01)
        question = runs.snapshot(run["id"])["question"]
        assert question["mode"] == "url"
        assert question["host"] == "example.test"
        assert effects == []
        with pytest.raises(ValueError):
            runs.answer(run["id"], "stale", action, None)
        with pytest.raises(ValueError):
            runs.answer(run["id"], question["id"], "accept", {"token": "secret"})
        runs.answer(run["id"], question["id"], action, None)
        with pytest.raises(ValueError):
            runs.answer(run["id"], question["id"], action, None)
        await runs.workers[run["id"]]
        assert runs.snapshot(run["id"])["status"] == "completed"
        assert effects == (["confirmed"] if action == "accept" else [])
        assert "example.test" not in str(session.records)
    finally:
        await runs.close()
