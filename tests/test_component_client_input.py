"""现代来源的资源/提示输入请求能由两代客户端回答。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import mcp.types as types
import pytest
from fastmcp import Client, Context, FastMCP
from test_gateway_client_capabilities import live_http
from test_proxy_cross_generation import single_era

from app.services.mcp_factory import MCPServerFactory
from app.services.legacy_input_bridge import answer_input


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", [None, "upstream-id"])
@pytest.mark.parametrize("version", ["2025-11-25", "2026-07-28"])
async def test_url_identifier_adaptation_preserves_original(identifier, version):
    request = types.ElicitRequest(
        method="elicitation/create",
        params=types.ElicitRequestURLParams(
            mode="url",
            message="fixture",
            url="https://example.invalid/approval",
            elicitation_id=identifier,
        ),
    )
    session = SimpleNamespace(
        protocol_version=version,
        send_request=AsyncMock(return_value=types.ElicitResult(action="cancel")),
    )
    assert (await answer_input(session, request)).action == "cancel"
    sent = session.send_request.await_args.args[0]
    assert request.params.elicitation_id == identifier
    assert sent.params.url == request.params.url
    if identifier is None and version == "2025-11-25":
        assert sent.params.elicitation_id
    else:
        assert sent.params.elicitation_id == identifier


def request_for(mode):
    if mode == "roots":
        return types.ListRootsRequest(method="roots/list")
    params = (
        types.ElicitRequestFormParams(
            message="fixture", requested_schema={"type": "object", "properties": {}}
        )
        if mode == "form"
        else types.ElicitRequestURLParams(
            mode="url",
            message="fixture",
            url="https://example.invalid/approval",
            elicitation_id="fixture",
        )
    )
    return types.ElicitRequest(method="elicitation/create", params=params)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["resource", "prompt"])
@pytest.mark.parametrize("downstream", ["auto", "legacy"])
@pytest.mark.parametrize("mode", ["roots", "form", "url"])
async def test_component_modern_input(kind, downstream, mode):
    source, completed, questions = FastMCP("modern inputs"), [], []

    def content(owner, ctx):
        if ctx.input_responses is None:
            return types.InputRequiredResult(
                request_state="fixture:" + owner,
                input_requests={"question": request_for(mode)},
            )
        assert ctx.request_state == "fixture:" + owner
        completed.append(owner)
        return ctx.input_responses["question"].model_dump_json(
            by_alias=True, exclude_none=True
        )

    @source.resource("fixture://input/{owner}")
    async def resource(owner: str, ctx: Context):
        return content(owner, ctx)

    @source.prompt()
    async def prompt(owner: str, ctx: Context):
        return content(owner, ctx)

    async def answer(message, response_type, params, context):
        questions.append(params.mode)
        if mode == "url":
            assert str(params.url) == "https://example.invalid/approval"
            if downstream == "legacy":
                assert params.elicitation_id
        return types.ElicitResult(action="cancel", _meta={"fixture": "preserved"})

    async with asyncio.timeout(20), live_http(
        single_era(source.http_app(path="/mcp", json_response=False), "modern", [])
    ) as target:
        proxy = MCPServerFactory.create_server(
            "input", {"type": "streamable-http", "url": target, "timeout": 5}
        )
        async with Client(
            proxy,
            mode=downstream,
            roots=["file:///fixture/alice"],
            elicitation_handler=answer,
        ) as client:
            if kind == "resource":
                result = await client.read_resource("fixture://input/alice")
                data = json.loads(result[0].text)
            else:
                result = await client.get_prompt("prompt", {"owner": "alice"})
                data = json.loads(result.messages[0].content.text)
    assert completed == ["alice"]
    if mode == "roots":
        assert data == {"roots": [{"uri": "file:///fixture/alice"}]}
        assert not questions
    else:
        assert data == {"action": "cancel", "_meta": {"fixture": "preserved"}}
        assert questions == [mode]
