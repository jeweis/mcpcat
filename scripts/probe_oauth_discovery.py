"""通过临时 MCPCat HTTP 实例验收真实上游 OAuth 发现，不使用用户凭证。"""

import argparse
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx2

from app.services.async_compat import operation_timeout
from app.services.config_service import ConfigService
from app.services.oauth_flow import OAuthFlowService
from scripts.probe_fastmcp_network import serve_app
from scripts.probe_network_commands import prepare


async def verify(api: httpx2.AsyncClient, key: str, url: str) -> None:
    """覆盖添加服务、后台发现、页面状态和配置持久化的真实调用路径。"""
    headers = {"Mcpcat-Key": key}
    response = await api.post(
        "/api/servers",
        headers=headers,
        json={
            "name": "oauth-discovery-probe",
            "config": {"type": "streamable-http", "url": url, "require_auth": True},
        },
    )
    assert response.status_code == 200, response.text
    async with operation_timeout(45):
        while True:
            response = await api.get(
                "/api/servers/oauth-discovery-probe", headers=headers
            )
            assert response.status_code == 200, response.text
            state = response.json()
            assert state["status"] != "failed", state.get("error")
            if state.get("auth_status") == "auth_required":
                break
            await asyncio.sleep(0.1)
    stored = ConfigService.get_server_config("oauth-discovery-probe")
    oauth = stored["oauth"]
    assert oauth["resource"] == url
    assert all(
        oauth.get(name)
        for name in ("issuer", "authorization_endpoint", "token_endpoint")
    )
    assert oauth.get("token") is None
    assert "S256" in oauth["code_challenge_methods_supported"]
    # 原授权入口在缺少配置时重新发现也必须成功，不注册或兑换凭证。
    rediscovered = await OAuthFlowService.discover_endpoints(url)
    assert rediscovered["issuer"] == oauth["issuer"]
    assert rediscovered["token_endpoint"] == oauth["token_endpoint"]
    assert (
        await api.delete("/api/servers/oauth-discovery-probe", headers=headers)
    ).status_code == 200
    assert ConfigService.get_server_config("oauth-discovery-probe") is None
    print(
        "PASS: HTTP add/query, auth_required, persisted OAuth endpoints, rediscovery, removal"
    )
    print(
        "Scope: discovery only; no client registration, user login, token exchange or business tool calls"
    )


async def main() -> None:
    """隔离数据库和 API Key；仅在指定地址进行无凭证发现。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="mcpcat-oauth-discovery-") as folder:
        app, _, write = prepare(Path(folder))
        async with serve_app(app) as base:
            async with httpx2.AsyncClient(base_url=base, timeout=50) as api:
                await verify(api, write, args.url)


if __name__ == "__main__":
    asyncio.run(main())
