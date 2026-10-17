"""在隔离环境验证 FastMCP 4.1.0 的公开接口，不访问业务数据。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from importlib.metadata import version

import httpx2
from fastmcp import Client, FastMCP
from fastmcp.experimental.transforms.code_mode import CodeMode, MontySandboxProvider
from fastmcp.server.extensions import MethodBinding, ServerExtension
from mcp.types import RequestParams
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS

PROTOCOL = "2026-07-28"
SKILLS = "io.modelcontextprotocol/skills"
SKILL_URI = "skill://probe/SKILL.md"
SKILL_TEXT = "---\nname: probe\ndescription: Test protocol discovery.\n---\n\n# Probe\n"


class LookupParams(RequestParams):
    """最小 get 参数类型。"""

    uri: str


class ProbeSkillsExtension(ServerExtension):
    """验证公开自定义方法注册，不作为产品实现。"""

    identifier = SKILLS

    def methods(self):
        """只注册现代协议的 list/get。"""
        return (
            MethodBinding(
                "skills/list",
                RequestParams,
                self.list_skills,
                protocol_versions=frozenset({PROTOCOL}),
            ),
            MethodBinding(
                "skills/get",
                LookupParams,
                self.get_skill,
                protocol_versions=frozenset({PROTOCOL}),
            ),
        )

    async def list_skills(self, context, params):
        """返回一致性验证清单。"""
        return {
            "resultType": "complete",
            "ttlMs": 0,
            "cacheScope": "private",
            "skills": [self.entry()],
        }

    async def get_skill(self, context, params):
        """返回明确 URI 的相同清单。"""
        if params.uri != SKILL_URI:
            raise ValueError("Unknown probe URI")
        return {
            "resultType": "complete",
            "ttlMs": 0,
            "cacheScope": "private",
            "skill": self.entry(),
        }

    @staticmethod
    def entry():
        """按实际 raw bytes 生成清单。"""
        content = SKILL_TEXT.encode()
        return {
            "uri": SKILL_URI,
            "frontmatter": {
                "name": "probe",
                "description": "Test protocol discovery.",
            },
            "resources": [
                {
                    "uri": SKILL_URI,
                    "size": len(content),
                    "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
                }
            ],
        }


async def probe_skills():
    """直接在 ASGI 上验证现代发现、扩展方法和 Resource。"""
    server = FastMCP("Protocol probe")
    server.add_extension(ProbeSkillsExtension())

    @server.resource(SKILL_URI, mime_type="text/markdown")
    def instructions() -> str:
        return SKILL_TEXT

    app = server.http_app(path="/mcp")
    results = {}
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://probe"
        ) as client:
            requests = [
                ("server/discover", {}),
                ("skills/list", {}),
                ("skills/get", {"uri": SKILL_URI}),
                ("resources/read", {"uri": SKILL_URI}),
            ]
            for index, (method, params) in enumerate(requests, 1):
                params = {
                    **params,
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": PROTOCOL,
                        "io.modelcontextprotocol/clientInfo": {
                            "name": "mcpcat-gate0",
                            "version": "1",
                        },
                        "io.modelcontextprotocol/clientCapabilities": {
                            "extensions": {SKILLS: {}},
                        },
                    },
                }
                headers = {
                    "MCP-Protocol-Version": PROTOCOL,
                    "Mcp-Method": method,
                    "Accept": "application/json, text/event-stream",
                }
                if "uri" in params:
                    headers["Mcp-Name"] = params["uri"]
                response = await client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": index,
                        "method": method,
                        "params": params,
                    },
                )
                response.raise_for_status()
                result = response.json()
                if "error" in result:
                    raise RuntimeError(f"{method}: {result['error']}")
                results[method] = result["result"]
    assert results["resources/read"]["contents"][0]["text"] == SKILL_TEXT
    assert results["skills/get"]["skill"] == results["skills/list"]["skills"][0]
    return results


async def probe_code_mode():
    """通过真实 Client 和 Monty 执行组合工具调用。"""
    server = FastMCP(
        "Code probe",
        transforms=[
            CodeMode(
                sandbox_provider=MontySandboxProvider(
                    limits={
                        "max_feed_duration_secs": 2,
                        "max_memory": 10_000_000,
                    }
                ),
                max_tool_calls=2,
            )
        ],
    )

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    async with Client(server) as client:
        tools = await client.list_tools()
        result = await client.call_tool(
            "execute",
            {
                "code": 'value = await call_tool("add", {"a": 2, "b": 3})\nreturn value',
            },
        )
        return {
            "tools": [tool.name for tool in tools],
            "execute_schema": next(
                t.input_schema for t in tools if t.name == "execute"
            ),
            "result": result.data,
        }


async def probe_legacy_http() -> dict[str, str]:
    """用原始旧握手验证框架协商，避免依赖新 Client 的自动降级。"""
    server = FastMCP("Legacy probe")
    app = server.http_app(path="/mcp", json_response=True)
    results = {}
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://probe"
        ) as client:
            for protocol in HANDSHAKE_PROTOCOL_VERSIONS:
                headers = {"Accept": "application/json, text/event-stream"}
                response = await client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": protocol,
                            "capabilities": {},
                            "clientInfo": {"name": "old-client", "version": "1"},
                        },
                    },
                )
                response.raise_for_status()
                result = response.json()["result"]
                assert result["protocolVersion"] == protocol
                headers["MCP-Protocol-Version"] = protocol
                headers["Mcp-Session-Id"] = response.headers["Mcp-Session-Id"]
                initialized = await client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                    },
                )
                initialized.raise_for_status()
                listed = await client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/list",
                        "params": {},
                    },
                )
                listed.raise_for_status()
                assert listed.json()["result"]["tools"] == []
                results[protocol] = "initialize + initialized + tools/list passed"
                await client.delete("/mcp", headers=headers)
    return results


async def main():
    """输出可保存的固定版本验证报告。"""
    report = {
        "versions": {
            name: version(name)
            for name in (
                "fastmcp",
                "fastmcp-slim",
                "mcp",
                "mcp-types",
                "httpx2",
                "pydantic",
                "starlette",
                "pydantic-monty",
            )
        },
        "client_signature": str(inspect.signature(Client)),
        "code_mode": await probe_code_mode(),
        "skills": await probe_skills(),
        "legacy_http": await probe_legacy_http(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
