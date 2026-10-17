"""机器认证不能被旧浏览器授权接口或迟到回调改写。"""

from copy import deepcopy
from unittest.mock import AsyncMock

import httpx2
import pytest
from test_oauth_callbacks import authorize, callback_app, complete

from app.api import oauth
from app.models.mcp_config import OAuthToken


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["authorize", "set-credentials"])
async def test_machine_source_rejects_interactive_mutation(callback_app, route):
    app, _, info, saved = callback_app
    info["config"]["oauth"]["grant_type"] = "client_credentials"
    before = deepcopy(info["config"])
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="https://gateway.fixture"
    ) as client:
        response = await client.post(
            f"/api/oauth/fixture/{route}",
            json={"client_id": "replacement", "client_secret": "replacement-secret"},
        )
    assert response.status_code == 409
    assert saved == []
    assert info["config"] == before


@pytest.mark.asyncio
async def test_auth_mode_switch_during_exchange_does_not_persist_token(
    callback_app, monkeypatch
):
    app, _, info, saved = callback_app

    async def exchange(*args, **kwargs):
        info["config"]["oauth"]["grant_type"] = "client_credentials"
        return OAuthToken(access_token="late-synthetic-token")

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="https://gateway.fixture"
    ) as client:
        pending = await authorize(client, info, "manual")
        saved.clear()
        monkeypatch.setattr(
            oauth.oauth_flow_service, "exchange_code_for_token", exchange
        )
        response = await complete(client, pending, "manual")
    assert response.status_code == 400
    assert saved == []
    assert "late-synthetic-token" not in str(info)


@pytest.mark.asyncio
async def test_pending_browser_callback_rejected_after_machine_switch(
    callback_app, monkeypatch
):
    app, _, info, saved = callback_app
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="https://gateway.fixture"
    ) as client:
        pending = await authorize(client, info, "manual")
        saved.clear()
        info["config"]["oauth"]["grant_type"] = "client_credentials"
        exchange = AsyncMock()
        monkeypatch.setattr(
            oauth.oauth_flow_service, "exchange_code_for_token", exchange
        )
        response = await complete(client, pending, "manual")
    assert response.status_code == 400
    exchange.assert_not_called()
    assert saved == []
