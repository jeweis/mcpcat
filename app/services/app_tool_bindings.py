"""Apps 工具绑定审核与逐调用检查，不转发网关秘密。"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from fastmcp import Client
from fastmcp.server.providers import FastMCPProvider
from sqlalchemy import delete, select
from starlette.concurrency import run_in_threadpool

from app.models.app_bindings import AppToolBinding
from app.services.app_audit import safe_app_actor
from app.services.app_content_service import AppDomainError, visible_version
from app.services.app_publication_service import app_version_lock
from app.services.config_service import ConfigService
from app.storage.app_models import AppToolBindingRecord
from app.storage.database import Database
from app.storage.runtime_identities import current_endpoint_identity, endpoint_identity
from app.storage.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedAppTool:
    """审核快照只保留明确来源和摘要；运行对象不写入数据库。"""

    binding: AppToolBinding
    server: Any
    identity: str
    digest: str
    tool: Any


class AppToolResolver:
    """使用已有服务运行链，授权回调每次重新验证当前身份。"""

    def __init__(
        self,
        database: Database,
        manager: Any,
        authorize: Callable[[], bool],
        allow_source: Callable[[str, dict], bool] | None = None,
        principal: Callable[[], str] | None = None,
    ) -> None:
        self.database, self.manager, self.authorize = database, manager, authorize
        self.allow_source = allow_source
        self.principal = principal

    def source(self, name: str) -> Any:
        """当前来源必须存在、开启且运行；不回退 Catalog 同名工具。"""
        if not self.authorize():
            raise AppDomainError("当前身份无权调用 App 工具", 403)
        config = ConfigService.get_server_config(name)
        info = self.manager.server_info.get(name, {})
        if (
            not config
            or not config.get("enabled", True)
            or info.get("status") != "running"
            or info.get("mcp") is None
        ):
            raise AppDomainError("绑定的 MCP 来源不可用")
        if info.get("gateway_component") in {"apps", "code-mode", "catalog"}:
            raise AppDomainError("App 不能递归绑定网关管理入口")
        if self.allow_source is not None and not self.allow_source(name, config):
            raise AppDomainError("当前身份无权访问绑定来源", 403)
        return info["mcp"]

    async def resolve(
        self, binding: AppToolBinding, *, register: bool = False
    ) -> ResolvedAppTool:
        """官方目录读取后再次校验来源和身份，保存确定 Schema 摘要。"""
        server = self.source(binding.server_name)
        identity = (
            endpoint_identity(self.database, "service", binding.server_name)
            if register
            else current_endpoint_identity(
                self.database, "service", binding.server_name
            )
        )
        if identity is None:
            raise AppDomainError("绑定来源身份已失效")
        try:
            tool = await asyncio.wait_for(
                FastMCPProvider(server).get_tool(binding.tool_name), timeout=30
            )
        except Exception:
            raise AppDomainError("绑定工具目录暂不可验证") from None
        if (
            self.source(binding.server_name) is not server
            or current_endpoint_identity(self.database, "service", binding.server_name)
            != identity
        ):
            raise AppDomainError("绑定来源在验证期间已改变")
        if tool is None:
            raise AppDomainError("绑定工具不存在")
        payload = {
            "identity": identity,
            "name": tool.name,
            "parameters": tool.parameters,
            "output_schema": tool.output_schema,
            "annotations": (
                tool.annotations.model_dump(mode="json") if tool.annotations else None
            ),
        }
        digest = hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        return ResolvedAppTool(binding, server, identity, digest, tool)

    def still_current(self, tool: ResolvedAppTool) -> bool:
        """提交/调用前再次确认运行代际，不创建退休来源身份。"""
        return (
            self.source(tool.binding.server_name) is tool.server
            and current_endpoint_identity(
                self.database, "service", tool.binding.server_name
            )
            == tool.identity
        )


async def replace_app_bindings(
    database: Database,
    resolver: AppToolResolver,
    *,
    slug: str,
    version: str,
    bindings: list[AppToolBinding],
) -> None:
    """先完整验证再原子替换，失败保留原草稿绑定。"""
    if len({item.alias for item in bindings}) != len(bindings):
        raise AppDomainError("App 工具别名重复", 400)
    resolved = [await resolver.resolve(item, register=True) for item in bindings]

    def save() -> None:
        if not resolver.authorize():
            raise AppDomainError("当前身份无权修改 App 绑定", 403)
        with UnitOfWork(database) as unit:
            version_id = visible_version(unit, slug, version, True).id
        with app_version_lock(database, version_id), UnitOfWork(database) as unit:
            row = visible_version(unit, slug, version, True)
            if row.status != "draft":
                raise AppDomainError("只能修改 App 草稿的工具绑定")
            if not all(resolver.still_current(item) for item in resolved):
                raise AppDomainError("绑定来源在提交期间已改变")
            unit.session.execute(
                delete(AppToolBindingRecord).where(
                    AppToolBindingRecord.app_version_id == row.id
                )
            )
            for item in resolved:
                record = unit.apps.bind_tool(
                    version_id=row.id, **item.binding.model_dump()
                )
                record.schema_sha256 = item.digest
            unit.commit()

    await run_in_threadpool(save)


def app_binding_rows(
    database: Database, slug: str, version: str, *, include_drafts: bool = False
) -> list[dict]:
    """只读取指定可见版本的明确绑定，不跨版本拼接授权。"""
    with UnitOfWork(database) as unit:
        row = visible_version(unit, slug, version, include_drafts)
        return [
            {
                "alias": item.alias,
                "server_name": item.server_name,
                "tool_name": item.tool_name,
                "digest": item.schema_sha256,
            }
            for item in unit.session.scalars(
                select(AppToolBindingRecord).where(
                    AppToolBindingRecord.app_version_id == row.id
                )
            )
        ]


async def publication_validator(
    database: Database, resolver: AppToolResolver, slug: str, version: str
) -> Callable[[list[AppToolBindingRecord]], None]:
    """在异步目录验证后，发布锁内校验精确绑定集合与运行代际。"""
    rows = app_binding_rows(database, slug, version, include_drafts=True)
    checked = {}
    for row in rows:
        item = await resolver.resolve(
            AppToolBinding(
                **{key: row[key] for key in ("alias", "server_name", "tool_name")}
            )
        )
        if item.digest != row["digest"]:
            raise AppDomainError("工具来源或 Schema 已变化，请重新审核草稿绑定")
        checked[row["alias"]] = item

    def validate(records: list[AppToolBindingRecord]) -> None:
        if not resolver.authorize():
            raise AppDomainError("当前身份无权发布 App", 403)
        if len(records) != len(checked):
            raise AppDomainError("工具绑定在审核期间已改变")
        for row in records:
            item = checked.get(row.alias)
            if (
                not item
                or item.binding.server_name != row.server_name
                or item.binding.tool_name != row.tool_name
                or item.digest != row.schema_sha256
                or not resolver.still_current(item)
            ):
                raise AppDomainError("工具绑定在审核期间已改变")

    return validate


async def call_app_tool(
    database: Database,
    resolver: AppToolResolver,
    *,
    slug: str,
    version: str,
    alias: str,
    arguments: dict,
) -> Any:
    """发布和实时权限均有效才调度；不记录参数、结果或上游错误正文。"""
    started, outcome = monotonic(), "denied"
    actor = safe_app_actor(resolver.principal)
    source = tool_name = None
    try:
        row, item = await _checked_binding(database, resolver, slug, version, alias)
        source, tool_name = row["server_name"], row["tool_name"]
        result = await _dispatch(
            database, resolver, slug, version, row, item, arguments
        )
        outcome = "error" if result.is_error else "success"
        app_binding_rows(database, slug, version)
        if not resolver.still_current(item):
            raise AppDomainError("来源在调用期间已变化，已完成操作不保证撤销")
        return result
    except AppDomainError as error:
        if outcome in {"success", "error"}:
            outcome = "authorization_changed_after_execution"
            raise AppDomainError(
                "App 当前授权已变化；部分操作可能已完成，请勿自动重试",
                error.status_code,
            ) from None
        raise
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except Exception:
        outcome = "error"
        raise AppDomainError(
            "App 工具调用失败；部分操作可能已完成，请勿自动重试"
        ) from None
    finally:
        audit_app_call(
            slug,
            version,
            alias if source else None,
            source,
            outcome,
            started,
            actor=actor,
            tool=tool_name,
        )


async def _checked_binding(
    database: Database, resolver: AppToolResolver, slug: str, version: str, alias: str
) -> tuple[dict, ResolvedAppTool]:
    rows = app_binding_rows(database, slug, version)
    row = next((item for item in rows if item["alias"] == alias), None)
    if row is None:
        raise AppDomainError("工具未被当前 App 版本授权", 403)
    item = await resolver.resolve(
        AppToolBinding(
            alias=alias, server_name=row["server_name"], tool_name=row["tool_name"]
        )
    )
    if item.digest != row["digest"]:
        raise AppDomainError("绑定工具来源或 Schema 已变化")
    return row, item


def audit_app_call(
    slug: str,
    version: str,
    alias: str | None,
    source: str | None,
    outcome: str,
    started: float,
    *,
    actor: str = "internal",
    tool: str | None = None,
) -> None:
    """已解析来源/别名和状态的安全摘要，不写参数或错误正文。"""
    logger.info(
        "App tool audit %s",
        {
            "app": slug,
            "version": version,
            "alias": alias,
            "source": source,
            "tool": tool,
            "actor": actor,
            "outcome": outcome,
            "duration_ms": int((monotonic() - started) * 1000),
        },
    )


async def _dispatch(
    database: Database,
    resolver: AppToolResolver,
    slug: str,
    version: str,
    row: dict,
    item: ResolvedAppTool,
    arguments: dict,
) -> Any:
    if row not in app_binding_rows(
        database, slug, version
    ) or not resolver.still_current(item):
        raise AppDomainError("App 授权在调用前已改变")
    async with Client(item.server, timeout=30) as client:
        latest = await resolver.resolve(item.binding)
        if latest.digest != item.digest:
            raise AppDomainError("绑定工具 Schema 在分发前已改变")
        if row not in app_binding_rows(
            database, slug, version
        ) or not resolver.still_current(item):
            raise AppDomainError("App 授权在分发前已改变")
        return await client.call_tool_mcp(item.binding.tool_name, arguments)
