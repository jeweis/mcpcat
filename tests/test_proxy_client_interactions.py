"""跨协议客户端目录/用户交互只交给当前客户端，不读取或请求本机资源。"""

import asyncio
import json

import mcp.types as types
import pytest
from fastmcp import Client, Context, FastMCP
from test_gateway_client_capabilities import live_http
from test_proxy_cross_generation import single_era

from app.services.mcp_factory import MCPServerFactory


@pytest.mark.asyncio
@pytest.mark.parametrize("downstream", ["auto", "legacy"])
@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
@pytest.mark.parametrize("kind", ["tool", "resource", "prompt"])
async def test_roots_and_elicitation_cross_protocol(downstream, enabled, action, kind):
    source, calls, questions = FastMCP("input fixture"), [], []

    async def interact(owner: str, ctx: Context):
        calls.append(owner)
        caps = ctx.session.client_capabilities
        if caps.roots is None or caps.elicitation is None:
            return {
                "roots": caps.roots is not None,
                "input": caps.elicitation is not None,
            }
        roots = await ctx.session.list_roots()
        results = []
        for params in (
            types.ElicitRequestFormParams(
                message=owner, requested_schema={"type": "object", "properties": {}}
            ),
            types.ElicitRequestURLParams(
                mode="url",
                message=owner,
                url="https://example.invalid/approval",
                elicitation_id="fixture",
            ),
        ):
            result = await ctx.session.send_request(
                types.ElicitRequest(method="elicitation/create", params=params),
                types.ElicitResult,
            )
            results.append(
                result.model_dump(mode="json", by_alias=True, exclude_none=True)
            )
        return {"roots": [str(root.uri) for root in roots.roots], "answers": results}

    source.tool(interact)

    @source.resource("fixture://interaction/{owner}")
    async def resource(owner: str, ctx: Context) -> str:
        return json.dumps(await interact(owner, ctx))

    @source.prompt()
    async def prompt(owner: str, ctx: Context) -> str:
        return json.dumps(await interact(owner, ctx))

    async def verify(url, owner):
        async def answer(message, response_type, params, context):
            assert message == owner
            questions.append((owner, params.mode))
            return types.ElicitResult(action=action, _meta={"fixture": owner})

        options = (
            {"roots": [f"file:///fixture/{owner}"], "elicitation_handler": answer}
            if enabled
            else {}
        )
        async with Client(url, mode=downstream, **options) as client:
            if kind == "tool":
                result = await client.call_tool("interact", {"owner": owner})
                data = result.data
            elif kind == "resource":
                result = await client.read_resource(f"fixture://interaction/{owner}")
                data = json.loads(result[0].text)
            else:
                result = await client.get_prompt("prompt", {"owner": owner})
                data = json.loads(result.messages[0].content.text)
        if enabled:
            assert data == {
                "roots": [f"file:///fixture/{owner}"],
                "answers": [{"action": action, "_meta": {"fixture": owner}}] * 2,
            }
        else:
            assert data == {"roots": False, "input": False}

    async with asyncio.timeout(30), live_http(
        single_era(source.http_app(path="/mcp", json_response=False), "legacy", [])
    ) as target:
        proxy = MCPServerFactory.create_server(
            "input", {"type": "streamable-http", "url": target, "timeout": 10}
        )
        async with live_http(proxy.http_app(path="/mcp", json_response=False)) as url:
            await asyncio.gather(verify(url, "alice"), verify(url, "bob"))
    assert sorted(calls) == ["alice", "bob"]
    assert len(questions) == (4 if enabled else 0)
