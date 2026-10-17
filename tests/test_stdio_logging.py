"""STDIO 日志跨代际投递、结构和原子进程释放。"""

import asyncio
import os
from pathlib import Path
import sys

import pytest
from fastmcp import Client
from test_gateway_client_capabilities import live_http

from app.services import mcp_factory
from app.services.mcp_factory import MCPServerFactory
from app.services.proxy_clients import make_proxy_client_factory


@pytest.mark.asyncio
@pytest.mark.parametrize("source_mode", ["auto", "legacy"])
@pytest.mark.parametrize("client_mode", ["auto", "legacy"])
@pytest.mark.parametrize("level", ["debug", "warning", "emergency"])
async def test_stdio_logs_preserve_payload_filter_and_release_process(
    monkeypatch,
    source_mode,
    client_mode,
    level,
):
    clients = []

    def tracked_factory(config):
        factory = make_proxy_client_factory(config)

        def create(**kwargs):
            client = factory(**kwargs)
            clients.append(client)
            return client

        return create

    monkeypatch.setattr(mcp_factory, "make_proxy_client_factory", tracked_factory)
    proxy = MCPServerFactory.create_server(
        "stdio-logging",
        {
            "type": "stdio",
            "command": sys.executable,
            "args": [
                str(Path(__file__).parent / "fixtures" / "mcp_stdio_logging.py"),
                source_mode,
            ],
            "timeout": 10,
        },
    )
    messages = []

    async def receive(message):
        messages.append(message)

    try:
        async with asyncio.timeout(30), live_http(proxy.http_app(path="/mcp")) as url:
            async with Client(url, mode=client_mode, log_handler=receive) as client:
                assert client.protocol_version == (
                    "2026-07-28" if client_mode == "auto" else "2025-11-25"
                )
                if client_mode == "legacy":
                    await client.set_logging_level(level)
                result = await client.call_tool(
                    "emit",
                    {"owner": "stdio-owner"},
                    meta={"io.modelcontextprotocol/logLevel": level},
                )
                assert result.data["protocol"] == (
                    "2026-07-28" if source_mode == "auto" else "2025-11-25"
                )
                with pytest.raises(ProcessLookupError):
                    os.kill(result.data["pid"], 0)
        levels = [
            "debug",
            "info",
            "notice",
            "warning",
            "error",
            "critical",
            "alert",
            "emergency",
        ]
        expected = levels[levels.index(level) :]
        if level != "emergency":
            expected.append("warning")
        assert [row.level for row in messages] == expected
        assert all(row.logger == "stdio-fixture" for row in messages)
        assert all(
            row.data == {"owner": "stdio-owner", "nested": [True, None]}
            for row in messages[: len(levels) - levels.index(level)]
        )
        if level != "emergency":
            assert messages[-1].data is None
    finally:
        for client in clients:
            await client.close()
