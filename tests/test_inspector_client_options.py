"""显式 Roots 模拟配置及正式测试会话的旧协议网络验证。"""

import asyncio

import pytest
from fastmcp import Context, FastMCP
from pydantic import ValidationError
from test_gateway_client_capabilities import live_http
from test_proxy_cross_generation import single_era

from app.api.inspector import CreateSessionRequest
from app.services.inspector_client_options import InspectorClientOptions
from app.services.inspector_service import InspectorService


def test_roots_opt_in_empty_and_independent_models() -> None:
    """缺省能力保持旧行为，空列表和两份独立模型语义明确。"""
    assert CreateSessionRequest(server_name="demo").client_options is None
    assert InspectorClientOptions().client_kwargs() == {}
    assert InspectorClientOptions(roots=[]).client_kwargs() == {"roots": []}
    options = InspectorClientOptions(roots=[{"uri": "file:///fixture/not-real"}])
    first, second = options.client_kwargs(), options.client_kwargs()
    first["roots"].clear()
    assert len(second["roots"]) == len(options.roots) == 1


@pytest.mark.parametrize("roots", [
    [{"uri": "https://example.com/"}],
    [{"uri": "/tmp/local"}],
    [{"uri": "file:///fixture", "name": "x" * 257}],
    [{"uri": "file:///fixture", "unknown": True}],
    [{"uri": "file:///fixture"}] * 33,
])
def test_invalid_roots_rejected_before_connection(roots: list) -> None:
    """拒绝错误协议、无界输入和未知属性。"""
    with pytest.raises(ValidationError):
        CreateSessionRequest(server_name="demo", client_options={"roots": roots})


@pytest.mark.asyncio
async def test_formal_inspector_sessions_keep_roots_separate() -> None:
    """真实 HTTP 通过正式会话工厂取得手工声明，不探测网关目录。"""
    server = FastMCP("roots fixture")

    @server.tool()
    async def declared_roots(ctx: Context) -> list[str]:
        result = await ctx.session.list_roots()
        return [str(root.uri) for root in result.roots]

    service = InspectorService()
    app = single_era(server.http_app(path="/mcp/demo", json_response=False), "legacy", [])
    async with asyncio.timeout(20), live_http(app) as endpoint:
        try:
            sessions = []
            for identity in ("alice", "bob"):
                options = InspectorClientOptions(roots=[{"uri": f"file:///fixture/{identity}"}])
                session_id = await service.create_session(
                    "demo", endpoint.removesuffix("/mcp"), client_options=options,
                )
                sessions.append((session_id, identity))
            for session_id, identity in sessions:
                result = await service.call_tool(session_id, "declared_roots", {})
                assert result["data"] == [f"file:///fixture/{identity}"]
        finally:
            for session_id in list(service.sessions):
                await service.close_session(session_id)
