from types import SimpleNamespace

import pytest

from app.models.mcp_config import CatalogConfig
from app.services.catalog_provider import CatalogProvider


@pytest.mark.asyncio
async def test_catalog_tool_cache_avoids_relisting(monkeypatch):
    provider = CatalogProvider(SimpleNamespace(server_info={}), CatalogConfig())
    calls = {"n": 0}

    async def compute():
        calls["n"] += 1
        return []

    monkeypatch.setattr(provider, "_compute_unfiltered_tools", compute)
    await provider.unfiltered_tools()
    await provider.unfiltered_tools()
    assert calls["n"] == 1  # 第二次命中缓存，不再重算
    await provider.force_refresh()
    assert calls["n"] == 2  # 刷新强制重算
