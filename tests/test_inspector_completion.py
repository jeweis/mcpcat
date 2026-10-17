"""参数补全适配边界，真实协议覆盖另由统一矩阵验收。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP
from mcp import types
from pydantic import ValidationError

from app.api.inspector import CompletionRequest
from app.services.inspector_workbench import complete_argument


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["prompts", "templates"])
async def test_completion_preserves_reference_context_and_result(kind):
    client = SimpleNamespace(
        server_capabilities=types.ServerCapabilities(completions={}),
        complete_mcp=AsyncMock(return_value=types.CompleteResult(
            completion=types.Completion(values=["Shanghai"], total=2, has_more=True),
        )),
    )
    result = await complete_argument(client, kind, "example", "city", "Sh", {"country": "CN"})
    assert result["supported"]
    assert result["completion"]["values"] == ["Shanghai"]
    assert result["completion"]["hasMore"] is True
    args, kwargs = client.complete_mcp.call_args
    assert args[0].type == ("ref/prompt" if kind == "prompts" else "ref/resource")
    assert args[1] == {"name": "city", "value": "Sh"}
    assert kwargs == {"context_arguments": {"country": "CN"}}


@pytest.mark.asyncio
async def test_unsupported_does_not_send_request():
    client = SimpleNamespace(server_capabilities=None, complete_mcp=AsyncMock())
    assert await complete_argument(client, "prompts", "a", "b", "", {}) == {"supported": False}
    client.complete_mcp.assert_not_called()


def test_completion_input_limits():
    base = dict(kind="prompts", reference="greet", argument="name")
    for change in ({"value": "a" * 4097}, {"context": {"a": "b" * 4097}}, {"kind": "tools"}):
        with pytest.raises(ValidationError):
            CompletionRequest(**(base | change))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("kind", ["prompts", "templates"])
async def test_real_protocol_completion(mode, kind):
    server = FastMCP("completion fixture")

    @server.completion
    def suggest(ref, argument, context):
        assert ref.type == ("ref/prompt" if kind == "prompts" else "ref/resource")
        assert context.arguments == {"country": "CN"}
        return [city for city in ["Shanghai", "Hangzhou"] if city.startswith(argument.value)]

    async with Client(server, mode=mode) as client:
        result = await complete_argument(client, kind, "example", "city", "Sh", {"country": "CN"})
        assert result["supported"]
        assert result["completion"]["values"] == ["Shanghai"]
