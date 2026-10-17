"""真实鉴权中间件下的 Inspector 会话与撤销回归。"""

import httpx2
import pytest
from unittest.mock import AsyncMock
from fastmcp import Client, FastMCP
from test_skill_content_workflow import READ, WRITE, content_app  # noqa: F401

from app.services.inspector_service import InspectorSession, inspector_service


@pytest.mark.asyncio
async def test_auth_diagnostics_before_connection_and_no_stale_result(content_app, monkeypatch):
    from app.api import inspector
    from app.services.server_manager import server_manager

    app, _ = content_app
    info = {"config": {"type": "streamable-http", "url": "https://example.test/mcp"}}
    monkeypatch.setitem(server_manager.server_info, "diagnostic-fixture", info)
    probe = AsyncMock(return_value={"status": "authorization_required"})
    monkeypatch.setattr(inspector, "diagnose_upstream", probe)
    path = "/api/inspector/auth/diagnostic-fixture"
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://fixture",
    ) as client:
        assert (await client.get(path)).status_code == 401
        headers = {"Mcpcat-Key": READ}
        response = await client.get(path, headers=headers)
        assert response.status_code == 200
        assert response.json()["gateway"] == "authenticated"
        probe.assert_not_called()
        response = await client.post(path + "/probe", headers=headers)
        assert response.json()["upstream"]["status"] == "authorization_required"
        assert READ not in response.text

        async def replace_source(_):
            info["config"]["url"] = "https://replacement.test/mcp"
            return {"status": "reachable"}

        monkeypatch.setattr(inspector, "diagnose_upstream", replace_source)
        assert (await client.post(path + "/probe", headers=headers)).status_code == 409


@pytest.mark.asyncio
async def test_read_identity_can_test_but_cannot_borrow_session(
    content_app,
):  # noqa: F811
    app, _ = content_app
    server = FastMCP("read-only fixture")
    session = InspectorSession("fixture", Client(server), READ)
    inspector_service.sessions[session.id] = session
    path = f"/api/inspector/sessions/{session.id}"
    try:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            assert (
                await client.get(path + "/capabilities", headers={"Mcpcat-Key": READ})
            ).status_code == 200
            assert (
                await client.get(path + "/tools", headers={"Mcpcat-Key": READ})
            ).status_code == 200
            for method, suffix, payload in [
                ("GET", "/skills", None),
                ("GET", "/tools", None),
                ("POST", "/call", {"tool": "x"}),
                ("DELETE", "", None),
                ("GET", "/records", None),
                ("GET", "/events", None),
                ("POST", "/subscription", {}),
                ("DELETE", "/subscription", None),
                ("POST", "/complete", {
                    "kind": "prompts", "reference": "greet", "argument": "name",
                }),
                ("GET", "/browse/resources", None),
                ("POST", "/inspect/prompts", {"name": "example"}),
                ("POST", "/apps/open", {"tool": "example"}),
                ("POST", "/runs", {"tool": "example"}),
                ("GET", "/runs/other", None),
                ("POST", "/runs/other/cancel", {}),
                (
                    "POST",
                    "/runs/other/answer",
                    {"question_id": "other", "action": "cancel"},
                ),
            ]:
                response = await client.request(
                    method, path + suffix, headers={"Mcpcat-Key": WRITE}, json=payload
                )
                assert response.status_code == 404
            assert (
                await client.post(
                    path + "/skills/get",
                    json={"uri": "skill://demo/SKILL.md"},
                    headers={"Mcpcat-Key": READ},
                )
            ).status_code == 422
            assert (
                await client.get(
                    path + "/capabilities", headers={"Mcpcat-Key": "revoked-key"}
                )
            ).status_code == 401
            summary = await client.get(path + "/records", headers={"Mcpcat-Key": READ})
            assert summary.status_code == 200
            assert READ not in summary.text
            assert all("arguments" not in row for row in summary.json()["records"])
            assert (
                await client.delete(path, headers={"Mcpcat-Key": READ})
            ).status_code == 200
    finally:
        inspector_service.sessions.pop(session.id, None)
