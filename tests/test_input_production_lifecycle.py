"""生产管理器撤权/停服/重启清理等待中的输入，旧状态不能进入新代际。"""

import asyncio

import httpx2
import mcp.types as types
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.shared.exceptions import MCPError
from test_gateway_client_capabilities import live_http
from test_modern_input_bridge import legacy_source
from test_proxy_cross_generation import single_era
from test_proxy_subscriptions import wait_for
from test_skill_content_workflow import READ, content_app

from app.application import create_app
from app.services.config_service import ConfigService
from app.services.inspector_service import inspector_service
from app.services.market_service import MarketService
from app.services.modern_input_bridge import ModernInputBridge
from app.services.server_manager import MCPServerManager
from app.storage.unit_of_work import UnitOfWork


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["revoke", "stop", "restart"])
async def test_waiting_input_production_lifecycle(content_app, monkeypatch, ending):
    _, database = content_app
    calls, closed = [], asyncio.Event()
    source = legacy_source(calls, closed)
    monkeypatch.setattr(MarketService, "refresh_async", lambda self: None)
    monkeypatch.setattr(inspector_service, "start_cleanup_task", lambda: None)
    async with asyncio.timeout(50), live_http(
        single_era(source.http_app(path="/mcp", json_response=False), "legacy", [])
    ) as upstream:
        assert ConfigService.add_server_to_config(
            "input-source",
            {
                "type": "streamable-http",
                "url": upstream,
                "require_auth": True,
                "timeout": 20,
            },
        )
        manager = MCPServerManager()
        app = create_app(database=database, server_manager=manager)
        async with live_http(app) as endpoint:
            bridge = next(
                item
                for item in manager.server_info["input-source"]["mcp"].middleware
                if isinstance(item, ModernInputBridge)
            )
            url = endpoint + "/input-source/"

            async def answer(messages, params, context):
                return "new client answer"

            async with Client(
                StreamableHttpTransport(url, headers={"Mcpcat-Key": READ}),
                sampling_handler=answer,
            ) as client:
                ask = await client.session.call_tool(
                    "sample",
                    {"value": "old"},
                    allow_input_required=True,
                )
                assert isinstance(ask, types.InputRequiredResult)
                assert len(bridge.pending) == 1 and not closed.is_set()
                if ending == "revoke":
                    with UnitOfWork(database) as unit:
                        unit.api_keys.update(READ, {"enabled": False})
                        unit.commit()
                elif ending == "stop":
                    assert await manager.stop_server("input-source")
                else:
                    assert await manager.restart_server("input-source")
                await asyncio.wait_for(closed.wait(), 5)
                await wait_for(lambda: not bridge.pending)
                reply = types.CreateMessageResult(
                    role="assistant",
                    model="fixture",
                    content=types.TextContent(type="text", text="stale"),
                )
                params = {
                    "name": "sample",
                    "arguments": {"value": "old"},
                    "requestState": ask.request_state,
                    "inputResponses": {
                        next(iter(ask.input_requests)): reply.model_dump(
                            mode="json",
                            by_alias=True,
                            exclude_none=True,
                        )
                    },
                }
                if ending == "restart":
                    with pytest.raises(MCPError):
                        await client.session.call_tool(
                            "sample",
                            {"value": "old"},
                            request_state=ask.request_state,
                            input_responses={next(iter(ask.input_requests)): reply},
                            allow_input_required=True,
                        )
                    assert (
                        await client.call_tool("sample", {"value": "new"})
                    ).content[0].text == "new client answer"
                    assert calls == ["old", "new"]
                else:
                    async with httpx2.AsyncClient() as probe:
                        result = await probe.post(
                            url,
                            headers={"Mcpcat-Key": READ},
                            json={
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "tools/call",
                                "params": params,
                            },
                        )
                        assert result.status_code == (
                            401 if ending == "revoke" else 503
                        )
                    assert calls == ["old"]
        assert not manager.lifecycle.background
