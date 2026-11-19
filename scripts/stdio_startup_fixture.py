"""离线启动夹具：真实 npx 本地包和确定性缓存冲突模拟器共用 MCP 程序。"""

import io
import json
import os
import sys
import tarfile
from pathlib import Path

SOURCE = """
import asyncio, json, os, time
import anyio
from pathlib import Path
from anyio.abc import ObjectReceiveStream
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage
import mcp.types as types
root = Path(os.environ["STARTUP_FIXTURE_ROOT"])
def event(name, **data):
    with (root / "events.jsonl").open("a") as out:
        out.write(json.dumps({"event": name, "pid": os.getpid(), "time": time.monotonic(), **data}) + "\\n")
event("spawn")
try:
    (root / "delayed").open("x").close()
    time.sleep(float(os.environ.get("STARTUP_FIXTURE_DELAY", "0")))
except FileExistsError:
    pass
class LegacyReceive(ObjectReceiveStream):
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
    async def receive(self):
        while True:
            item = await self.reader.receive()
            message = item.message if isinstance(item, SessionMessage) else None
            if isinstance(message, types.JSONRPCRequest) and message.method == "server/discover":
                await self.writer.send(SessionMessage(types.JSONRPCError(
                    jsonrpc="2.0", id=message.id,
                    error=types.ErrorData(code=types.METHOD_NOT_FOUND, message="Legacy fixture"),
                )))
            else:
                return item
    async def aclose(self):
        await self.reader.aclose()
async def list_tools(ctx, params):
    return types.ListToolsResult(tools=[types.Tool(name="identity", input_schema={
        "type": "object", "properties": {"pause": {"type": "number"}},
    })])
async def identity(ctx, params):
    roots = await ctx.session.list_roots()
    values = [str(item.uri) for item in roots.roots]
    event("tool_start", roots=values)
    if os.environ.get("STARTUP_FIXTURE_BARRIER"):
        (root / f"tool-ready-{os.getpid()}").touch()
        with anyio.fail_after(15):
            while len(list(root.glob("tool-ready-*"))) < 2:
                await asyncio.sleep(0.02)
    await asyncio.sleep(params.arguments.get("pause", 0.2))
    event("tool_end", roots=values)
    data = {"pid": os.getpid(), "roots": values}
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(data))],
                               structured_content=data)
async def main():
    server = Server("cold startup fixture", on_list_tools=list_tools, on_call_tool=identity)
    async with stdio_server() as (reader, writer):
        await server.run(LegacyReceive(reader, writer), writer, server.create_initialization_options())
asyncio.run(main())
"""

INSTALL = """
const fs = require('fs');
const path = require('path');
const root = process.env.STARTUP_FIXTURE_ROOT;
const marker = path.join(root, 'installing');
try { fs.closeSync(fs.openSync(marker, 'wx')); }
catch (error) { console.error('npm error ENOTEMPTY: concurrent fixture install'); process.exit(1); }
setTimeout(() => {
  fs.appendFileSync(path.join(root, 'installs.txt'), 'installed\\n');
  fs.unlinkSync(marker);
}, 500);
"""


def source_config(root: Path, *, delay: float = 0) -> dict:
    """仅传递合成路径/延迟，不接受用户数据库或凭证。"""
    root.mkdir(parents=True, exist_ok=True)
    source = root / "source.py"
    source.write_text(SOURCE)
    return {
        "type": "stdio",
        "command": sys.executable,
        "args": [str(source)],
        "env": {"STARTUP_FIXTURE_ROOT": str(root), "STARTUP_FIXTURE_DELAY": str(delay)},
    }


def fake_npx_config(root: Path, *, delay: float = 0) -> dict:
    """启动重叠时必然失败，用于验证门闩，不依赖 npm 偶现竞态。"""
    config = source_config(root, delay=delay)
    executable = root / "npx"
    executable.write_text(f"#!{sys.executable}\n" + """
import os, sys, time
from pathlib import Path
root = Path(os.environ["STARTUP_FIXTURE_ROOT"])
marker = root / "starting"
try:
    marker.open("x").close()
except FileExistsError:
    print("npm error ENOTEMPTY: overlapping startup", file=sys.stderr)
    sys.exit(1)
time.sleep(0.1)
marker.unlink()
os.execv(sys.executable, [sys.executable, str(root / "source.py")])
""")
    executable.chmod(0o755)
    config["command"] = str(executable)
    config["args"] = []
    return config


def real_npx_config(root: Path, *, delay: float = 0, npx: str = "npx") -> dict:
    """生成无外部依赖的 tar 包，在空 npm 缓存中真实安装并启动。"""
    config = source_config(root, delay=delay)
    manifest = {
        "name": "mcpcat-startup-fixture",
        "version": "1.0.0",
        "bin": {"mcpcat-startup-fixture": "bin.js"},
        "scripts": {"postinstall": "node install.cjs"},
    }
    launcher = "#!/usr/bin/env node\n" + (
        "const p=require('child_process').spawnSync("
        + json.dumps(sys.executable)
        + ", ["
        + json.dumps(str(root / "source.py"))
        + "], {stdio:'inherit',env:process.env}); process.exit(p.status ?? 1);\n"
    )
    archive = root / "fixture.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, content in {
            "package.json": json.dumps(manifest),
            "bin.js": launcher,
            "install.cjs": INSTALL,
        }.items():
            data = content.encode()
            info = tarfile.TarInfo("package/" + name)
            info.size, info.mode = len(data), 0o755 if name == "bin.js" else 0o644
            tar.addfile(info, io.BytesIO(data))
    config["command"] = npx
    config["args"] = ["-y", "--package", str(archive), "mcpcat-startup-fixture"]
    config["env"].update(
        {
            "npm_config_cache": str(root / "npm-cache"),
            "npm_config_audit": "false",
            "npm_config_fund": "false",
            "npm_config_update_notifier": "false",
            "npm_config_registry": "http://127.0.0.1:9",
            # npm 11 本地包限制需要显式允许安装脚本；npm 10 忽略此配置。
            "npm_config_ignore_scripts": "false",
        }
    )
    return config


def events(root: Path) -> list[dict]:
    """读取夹具证据，不读取进程环境或用户配置。"""
    path = root / "events.jsonl"
    return (
        [json.loads(row) for row in path.read_text().splitlines()]
        if path.exists()
        else []
    )
