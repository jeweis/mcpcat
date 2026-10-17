"""Catalog 直调也加入来源生命周期，不能绕过管理器的停止/重启清理。"""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from typing import TypeVar

from app.services.catalog_app_sources import CatalogAppSource, CatalogAppSources
from app.services.mcp_request_lifecycle import service_request

T = TypeVar("T")


async def source_operation(
    sources: CatalogAppSources,
    source: CatalogAppSource,
    operation: Callable[[], Awaitable[T]],
) -> T:
    """在实际调用任务登记来源；停止可取消调用，收尾后清除停止标记。"""
    registry = getattr(sources.manager, "lifecycle", None)
    try:
        async with (
            service_request(registry, source.name) if registry else nullcontext()
        ):
            if not sources.current(source):
                raise ValueError("Catalog App source unavailable")
            return await operation()
    finally:
        if registry is not None:
            registry.stopping_requests.discard(asyncio.current_task())
