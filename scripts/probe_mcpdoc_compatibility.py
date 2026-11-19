"""原始 mcpdoc JSON 的完整 HTTP 测试台验收；无需生产凭证。"""

import asyncio
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx2

from app.services.config_service import ConfigService
from scripts.probe_fastmcp_network import serve_app
from scripts.probe_network_commands import prepare

CONFIG = {
    "type": "stdio",
    "enabled": True,
    "require_auth": False,
    "timeout": 30,
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


async def use(api, key, name):
    """真实创建会话、发现工具和关闭，不调用有业务副作用的工具。"""
    headers = {"Mcpcat-Key": key}
    response = await api.post(
        "/api/inspector/sessions", json={"server_name": name}, headers=headers
    )
    if response.status_code != 200:
        return response
    session = response.json()["session_id"]
    try:
        return await api.get(
            f"/api/inspector/sessions/{session}/tools", headers=headers
        )
    finally:
        await api.delete(f"/api/inspector/sessions/{session}", headers=headers)


async def verify(app, api, read, write):
    """原配置并发成功、约束不覆盖、原配置不改写、兼容准备只做一次。"""
    headers = {"Mcpcat-Key": write}
    response = await api.post(
        "/api/servers",
        json={"name": "docs", "config": deepcopy(CONFIG)},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    replies = await asyncio.gather(use(api, read, "docs"), use(api, write, "docs"))
    for reply in replies:
        assert reply.status_code == 200, reply.text
        assert {tool["name"] for tool in reply.json()["tools"]} == {
            "list_doc_sources",
            "fetch_docs",
        }
    assert ConfigService.load_mcp_servers_config()["docs"]["args"] == CONFIG["args"]
    logs = (
        await api.get(
            "/api/admin/logs",
            headers=headers,
            params={"server": "docs", "search": "mcpdoc", "limit": 200},
        )
    ).json()
    applied = [
        row
        for row in logs["items"]
        if row["message"]
        in {"已自动应用 mcpdoc 启动兼容规则", "使用已准备的 mcpdoc 兼容环境"}
    ]
    assert applied, "生效规则应能从诊断日志查询"
    override = deepcopy(CONFIG)
    override["args"] = ["--with", "mcp>=2", *CONFIG["args"]]
    assert (
        await api.post(
            "/api/servers",
            json={"name": "override", "config": override},
            headers=headers,
        )
    ).status_code == 200
    failure = await use(api, read, "override")
    assert failure.status_code >= 400 and "自定义依赖" in failure.text, failure.text
    print(
        "PASS: original JSON, cold install, concurrent READ/WRITE tools discovery, original config retained, compatibility visible, explicit constraints respected and explained",
        flush=True,
    )


async def main():
    """所有数据库、配置和日志均在临时实例中。"""
    with TemporaryDirectory(prefix="mcpcat-mcpdoc-compat-") as folder:
        app, read, write = prepare(Path(folder))
        async with serve_app(app) as url:
            app.state.port = int(url.rsplit(":", 1)[1])
            async with httpx2.AsyncClient(base_url=url, timeout=180) as api:
                await verify(app, api, read, write)
        ConfigService.reset_database()


if __name__ == "__main__":
    asyncio.run(main())
