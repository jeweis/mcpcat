"""在临时回环端口验证真实 HTTP/SSE，不访问业务数据或启动上游程序。"""

import asyncio
import json
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from anyio import fail_after
from fastmcp import Client, FastMCP
from fastmcp.client.transports import SSETransport
from starlette.types import ASGIApp


@asynccontextmanager
async def serve(
    server: FastMCP,
    transport: str,
    session_idle_timeout: float | None | str = "auto",
    json_response: bool = False,
) -> AsyncIterator[str]:
    """启动仅本机可达的测试端点并确保退出清理。"""
    app = server.http_app(
        path="/mcp",
        transport=transport,
        session_idle_timeout=session_idle_timeout,
        json_response=json_response,
    )
    async with serve_app(app) as root:
        yield root + "/mcp"


@asynccontextmanager
async def serve_app(app: ASGIApp) -> AsyncIterator[str]:
    """通用 ASGI 回环夹具；只在上下文期间监听临时端口。"""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    config = uvicorn.Config(
        app,
        log_level="error",
        timeout_graceful_shutdown=2,
    )
    runner = uvicorn.Server(config)
    task = asyncio.create_task(runner.serve(sockets=[listener]))
    try:
        with fail_after(5):
            while not runner.started:
                if task.done():
                    await task
                    raise RuntimeError("Temporary server did not start")
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        runner.should_exit = True
        await task
        listener.close()


async def main() -> None:
    """用框架默认协商和 legacy 模式调用相同测试工具。"""
    server = FastMCP("Network probe")

    @server.tool()
    def echo(value: str) -> str:
        """Return input unchanged."""
        return value

    report = []
    for transport in ("http", "sse"):
        async with serve(server, transport) as url:
            modes = ("auto", "legacy") if transport == "http" else ("legacy",)
            for mode in modes:
                target = SSETransport(url) if transport == "sse" else url
                async with Client(target, mode=mode) as client:
                    result = await client.call_tool("echo", {"value": "passed"})
                    assert result.data == "passed"
                    report.append(
                        {
                            "transport": transport,
                            "mode": mode,
                            "protocol": client.protocol_version,
                            "result": result.data,
                        }
                    )
            if transport == "http" and os.environ.get("PROBE_MCPORTER") == "1":
                config = Path(__file__).parent / "fixtures" / "mcporter-empty.json"
                process = await asyncio.create_subprocess_exec(
                    "npm",
                    "exec",
                    "--offline",
                    "--yes",
                    "--package=mcporter@0.13.7",
                    "--",
                    "mcporter",
                    "--config",
                    str(config.resolve()),
                    "call",
                    f"{url}.echo",
                    "value=passed",
                    "--allow-http",
                    cwd="/private/tmp",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(), timeout=30
                    )
                except BaseException:
                    process.kill()
                    await process.wait()
                    raise
                if process.returncode:
                    raise RuntimeError(stderr.decode())
                assert "passed" in stdout.decode()
                report.append(
                    {
                        "transport": "http",
                        "client": "mcporter@0.13.7",
                        "result": "passed",
                    }
                )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
