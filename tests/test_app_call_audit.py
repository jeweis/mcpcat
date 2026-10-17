"""真实 REST/MCP Apps 调用审计标识与秘密隔离。"""

import logging

import httpx2
import pytest
from test_apps_mcp_gateway import bound_source, gateway
from test_skill_content_workflow import READ, WRITE, content_app
from test_skills_mcp_gateway import rpc

from app.services.app_audit import app_audit_actor, safe_app_actor


@pytest.mark.asyncio
@pytest.mark.parametrize("rest", [False, True])
async def test_current_caller_audited_without_key_arguments_or_results(
    content_app, monkeypatch, tmp_path, caplog, rest
):
    _, database = content_app
    app = gateway(database, monkeypatch, tmp_path)

    def value(value: int) -> dict:
        return {"saved": value, "secret": "private-result-sentinel"}

    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            name = await bound_source(app, client, value)
            with caplog.at_level(logging.INFO, logger="app.services.app_tool_bindings"):
                for key in (READ, WRITE):
                    if rest:
                        response = await client.post(
                            "/api/apps/bound/versions/1.0.0/preview-call",
                            headers={"Mcpcat-Key": key},
                            json={
                                "name": name,
                                "arguments": {"value": 98123},
                                "confirmed": True,
                            },
                        )
                    else:
                        response = await rpc(
                            client,
                            "tools/call",
                            {
                                "name": name,
                                "arguments": {"value": 98123},
                            },
                            key=key,
                            path="apps",
                        )
                    assert response.status_code == 200, response.text
                    assert "private-result-sentinel" in response.text
    events = [
        record.args for record in caplog.records if record.msg == "App tool audit %s"
    ]
    assert [event["actor"] for event in events] == [
        app_audit_actor(database, key) for key in (READ, WRITE)
    ]
    assert len({event["actor"] for event in events}) == 2
    assert all(
        event["tool"] == "value" and event["source"] == "math" for event in events
    )
    assert all(event["outcome"] == "success" for event in events)
    for secret in (READ, WRITE, "98123", "private-result-sentinel"):
        assert secret not in caplog.text


def test_untrusted_or_unavailable_actor_never_leaks_into_audit(content_app):
    _, database = content_app
    assert app_audit_actor(database, "") == "anonymous"
    assert app_audit_actor(database, "unknown-credential-sentinel") == "unknown"
    assert safe_app_actor(lambda: READ) == "unknown"
    assert safe_app_actor(lambda: "key:7\nforged") == "unknown"
    assert safe_app_actor(lambda: 123) == "unknown"
    assert safe_app_actor(lambda: "key:7") == "key:7"
    assert safe_app_actor(None) == "internal"

    def broken():
        raise ValueError("private-exception-sentinel")

    assert safe_app_actor(broken) == "unknown"
