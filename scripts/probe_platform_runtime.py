"""多类型 MCP 的完整 HTTP 测试台与运行管理验收，全部使用临时数据。"""

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx2
from fastmcp import FastMCP

from scripts.probe_fastmcp_network import serve_app
from scripts.probe_network_commands import prepare


async def use(api, key, name, *, call=True):
    """按用户真实路径创建会话、发现工具并调用无副作用的 echo。"""
    headers = {"Mcpcat-Key": key}
    response = await api.post(
        "/api/inspector/sessions", json={"server_name": name}, headers=headers
    )
    assert response.status_code == 200, response.text
    session = response.json()["session_id"]
    try:
        tools = await api.get(
            f"/api/inspector/sessions/{session}/tools", headers=headers
        )
        assert tools.status_code == 200 and tools.json()["tools"], tools.text
        if call:
            result = await api.post(
                f"/api/inspector/sessions/{session}/call",
                json={"tool": "echo", "arguments": {"message": key[-6:]}},
                headers=headers,
            )
            assert result.status_code == 200 and key[-6:] in result.text, result.text
        return session
    finally:
        await api.delete(f"/api/inspector/sessions/{session}", headers=headers)


def source_configs(remote: str, sse: str, real_packages: bool) -> dict:
    """建立覆盖 Python/Node、本地/远程、传统/现代协议的配置。"""
    fixtures = Path(__file__).with_name("fixtures")
    sources = {
        "python-legacy": {
            "type": "stdio",
            "command": sys.executable,
            "args": [str(fixtures / "platform_legacy.py")],
            "env": {"CLOSE_DISCOVER": "1"},
        },
        "node-legacy": {
            "type": "stdio",
            "command": shutil.which("node"),
            "args": [str(fixtures / "platform_node.mjs")],
            "env": {},
        },
        "http-modern": {"type": "streamable-http", "url": remote + "/mcp"},
        "sse-legacy": {"type": "sse", "url": sse + "/sse"},
    }
    if real_packages:
        sources["context7"] = {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@upstash/context7-mcp@4.7.3"],
            "env": {},
        }
        sources["mcpdoc"] = {
            "type": "stdio",
            "command": "uvx",
            "args": [
                "--from",
                "mcpdoc",
                "mcpdoc",
                "--urls",
                "AgentScopeJava:https://java.agentscope.io/llms.txt",
            ],
            "env": {},
        }
    return sources


def verify_dependencies(name: str, state: dict) -> None:
    """确认记录的依赖版本来自正确环境，避免把混合缓存当作活动依赖。"""
    environment = state["environment"]
    row = next(
        item
        for item in environment["revisions"]
        if item["id"] == environment["current"]
    )
    assert row["validated"] and row["packages"], row
    if name == "context7":
        assert any(
            item["name"] == "@upstash/context7-mcp" and item["version"] == "4.7.3"
            for item in row["packages"]
        )
    else:
        assert any(
            item["name"] == "mcp" and item["version"].startswith("1.")
            for item in row["packages"]
        )
        if row.get("packages_scope") == "resolved":
            assert not any(
                item["name"] == "mcp" and item["version"].startswith("2.")
                for item in row["packages"]
            )
    print(
        "PASS dependencies:",
        name,
        row.get("packages_scope"),
        len(row["packages"]),
        flush=True,
    )


async def verify_environments(api, read: str, write: str) -> None:
    """验证修订切换与角色隔离，不改变已运行的业务会话。"""
    headers = {"Mcpcat-Key": write}
    path = "/api/admin/runtime/python-legacy"
    old = (await api.get(path, headers=headers)).json()["environment"]["current"]
    fresh = await api.post(path + "/environment", json={}, headers=headers)
    assert fresh.status_code == 200 and fresh.json()["environment"]["current"] != old
    await use(api, read, "python-legacy")
    back = await api.post(
        path + "/environment", json={"revision": old}, headers=headers
    )
    assert back.status_code == 200
    await use(api, read, "python-legacy")
    assert (await api.get("/api/admin/runtime")).status_code == 401
    assert (
        await api.get("/api/admin/runtime", headers={"Mcpcat-Key": read})
    ).status_code == 403
    print(
        "PASS: environment refresh/rollback, admin guards, independent READ/WRITE sessions",
        flush=True,
    )


async def verify(app, api, read, write, remote, sse, real_packages):
    """覆盖旧/新协议、Python/Node、HTTP/SSE、环境切换和权限。"""
    headers = {"Mcpcat-Key": write}
    for name, config in source_configs(remote, sse, real_packages).items():
        assert config.get("command", True), "验收需要 Node.js"
        response = await api.post(
            "/api/servers",
            json={"name": name, "config": {**config, "require_auth": True}},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        await asyncio.gather(
            use(api, read, name, call=name not in {"context7", "mcpdoc"}),
            use(api, write, name, call=name not in {"context7", "mcpdoc"}),
        )
        state = (await api.get(f"/api/admin/runtime/{name}", headers=headers)).json()
        assert state["attempts"], state
        if name in {"context7", "mcpdoc"}:
            verify_dependencies(name, state)
        print("PASS source:", name, flush=True)
    await verify_environments(api, read, write)


async def main():
    """可选服务模式供真实浏览器验证管理界面。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-packages", action="store_true")
    parser.add_argument("--serve", type=Path)
    args = parser.parse_args()
    remote = FastMCP("modern remote")

    @remote.tool()
    def echo(message: str) -> dict:
        return {"message": message}

    with TemporaryDirectory(prefix="mcpcat-platform-") as folder:
        app, read, write = prepare(Path(folder))
        async with serve_app(remote.http_app(path="/mcp")) as url, serve_app(
            remote.http_app(path="/sse", transport="sse")
        ) as sse, serve_app(app) as base:
            app.state.port = int(base.rsplit(":", 1)[1])
            async with httpx2.AsyncClient(base_url=base, timeout=180) as api:
                await verify(app, api, read, write, url, sse, args.real_packages)
            if args.serve:
                args.serve.write_text(
                    json.dumps({"url": base, "read": read, "write": write})
                )
                args.serve.chmod(0o600)
                try:
                    await asyncio.Event().wait()
                finally:
                    args.serve.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
