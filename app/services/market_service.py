import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

MARKET_DATA_URL_PRIMARY = "https://raw.githubusercontent.com/jeweis/mcpcat-market/refs/heads/main/mcp_market.json"
MARKET_DATA_URL_FALLBACK = "https://gitee.com/jeweis/mcpcat-market/raw/main/mcp_market.json"
MARKET_DATA_TTL = 600

class MarketService:
    def __init__(self, remote_url: str, remote_url_fallback: str, ttl_seconds: int, local_path: Path):
        self.remote_url = remote_url
        self.remote_url_fallback = remote_url_fallback
        self.ttl_seconds = ttl_seconds
        self.local_path = local_path
        self._cache: Optional[Dict[str, Any]] = None
        self._last_fetch_at: Optional[float] = None
        self._lock = asyncio.Lock()

    async def get_market(self) -> Dict[str, Any]:
        if self._cache is None:
            self._cache = self._load_local_fallback()
            self._last_fetch_at = None
            self.refresh_async()
            return self._cache

        if self._is_expired():
            self.refresh_async()

        return self._cache

    def refresh_async(self) -> None:
        asyncio.create_task(self._refresh())

    async def _refresh(self) -> None:
        if self._lock.locked():
            return

        async with self._lock:
            data = await self._fetch_from_urls()
            if data:
                self._cache = data
                self._last_fetch_at = asyncio.get_event_loop().time()
                logger.info("Market data fetched from remote successfully")
            elif self._cache is None:
                self._cache = self._load_local_fallback()

    async def _fetch_from_urls(self) -> Optional[Dict[str, Any]]:
        urls = [self.remote_url, self.remote_url_fallback]
        for url in urls:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    data = response.json()
                    if isinstance(data, dict):
                        return data
                    raise ValueError("远程市场数据格式无效")
            except Exception as e:
                logger.error(f"获取远程 MCP 市场失败 ({url}): {e}")
        return None

    def _load_local_fallback(self) -> Dict[str, Any]:
        if not self.local_path.exists():
            return {"servers": []}
        try:
            return json.loads(self.local_path.read_text())
        except Exception as e:
            logger.error(f"读取本地市场数据失败: {e}")
            return {"servers": []}

    def _is_expired(self) -> bool:
        if self._last_fetch_at is None:
            return True
        now = asyncio.get_event_loop().time()
        return (now - self._last_fetch_at) > self.ttl_seconds
