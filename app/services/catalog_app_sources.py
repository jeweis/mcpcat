"""Catalog Apps 来源快照；地址/目录对象不替代每次请求的实时授权。"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.services.catalog_app_identity import CatalogAppIdentity
from app.services.config_service import ConfigService
from app.storage.database import Database
from app.storage.runtime_identities import current_endpoint_identity


@dataclass(frozen=True)
class CatalogAppSource:
    """运行对象只在内存持有；不能拿历史快照恢复已退休来源。"""

    name: str
    server: Any
    identity: CatalogAppIdentity


class CatalogAppSources:
    """先授权再访问来源身份；请求只能读身份，不隐式创建/恢复身份。"""

    def __init__(
        self,
        database: Database,
        manager: Any,
        *,
        available: Callable[[], bool],
        authorize: Callable[[dict], bool],
        config_loader: Callable[[str], dict | None] = ConfigService.get_server_config,
    ) -> None:
        self.database, self.manager = database, manager
        self.available, self.authorize = available, authorize
        self.config_loader = config_loader
        self._generations: dict[str, CatalogAppSource] = {}

    def source(self, name: str) -> CatalogAppSource | None:
        """复核开启/纳入/OAuth/身份；停用、撤权和运行对象替换即时失效。"""
        if not self.available():
            return None
        config = self.config_loader(name)
        info = self.manager.server_info.get(name, {})
        if (
            config is None
            or not config.get("enabled", True)
            or not config.get("expose_in_catalog", False)
            or config.get("oauth")
            or info.get("gateway_component") in {"apps", "catalog", "code-mode"}
            or info.get("status") != "running"
            or info.get("mcp") is None
            or not self.authorize(config)
        ):
            return None
        identity = current_endpoint_identity(self.database, "service", name)
        if identity is None:
            return None
        previous = self._generations.get(name)
        if (
            previous is None
            or previous.server is not info["mcp"]
            or previous.identity.source != identity
        ):
            previous = CatalogAppSource(
                name, info["mcp"], CatalogAppIdentity(identity, str(uuid4()))
            )
            self._generations[name] = previous
        return previous

    def current(self, source: CatalogAppSource) -> bool:
        """异步查目录/读取/调用后重复检查，旧 Tool/Resource 不延长权限。"""
        current = self.source(source.name)
        return bool(
            current is not None
            and current.server is source.server
            and current.identity == source.identity
        )

    def sources(self) -> list[CatalogAppSource]:
        """目录每次重建，不缓存身份权限或把未知服务写回数据库。"""
        result = []
        for name in tuple(self.manager.server_info):
            source = self.source(name)
            if source is not None:
                result.append(source)
        active = set(self.manager.server_info)
        self._generations = {
            name: item for name, item in self._generations.items() if name in active
        }
        return result
