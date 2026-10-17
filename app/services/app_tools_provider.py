"""Apps 打开/回调工具 Provider，visibility 只作受众标记而非授权。"""

from collections.abc import Awaitable, Callable, Sequence

from fastmcp.exceptions import ToolError
from fastmcp.server.providers import Provider
from fastmcp.tools import Tool, ToolResult
from mcp_types import ToolAnnotations
from pydantic import PrivateAttr

from app.models.app_bindings import AppToolBinding
from app.services.app_content_service import AppDomainError
from app.services.app_resource_registry import AppResourceRegistry, app_callback_name
from app.services.app_resource_uri import AppResourceURI
from app.services.app_tool_bindings import (
    AppToolResolver,
    app_binding_rows,
    call_app_tool,
)
from app.services.code_mode_provider import GATEWAY_COMPONENT_META


def app_open_name(identity: AppResourceURI) -> str:
    """打开动作与任意用户别名共享不同名称类别。"""
    return "open_" + app_callback_name(identity.app_id, identity.version, "open")


class AppGatewayTool(Tool):
    """参数 Schema 来自真实源工具，调度函数每次验证绑定和权限。"""

    _dispatch: Callable[[dict], Awaitable[ToolResult]] = PrivateAttr()

    async def run(self, arguments: dict) -> ToolResult:
        """直猜名称也不绕过当前 Registry/绑定授权。"""
        try:
            return await self._dispatch(arguments)
        except (ValueError, OSError) as error:
            if isinstance(error, AppDomainError):
                raise ToolError(str(error)) from None
            raise ToolError("App tool unavailable") from None


class AppToolsProvider(Provider):
    """每个工具固定 App ID/版本；未发布或撤销工具不再可调用。"""

    def __init__(
        self, registry: AppResourceRegistry, resolver: AppToolResolver
    ) -> None:
        super().__init__()
        self.registry, self.resolver = registry, resolver

    def _open(self, identity: AppResourceURI, slug: str) -> Tool:
        async def dispatch(arguments: dict) -> ToolResult:
            if arguments:
                raise AppDomainError("App 打开动作不接受未声明参数", 400)
            _, payload, _ = await self.registry.entry(identity.encode())
            return ToolResult(
                content=f"App {slug} {identity.version}. Interactive rendering requires an MCP Apps host.",
                structured_content=payload,
            )

        tool = AppGatewayTool(
            name=app_open_name(identity),
            description=f"Open App {slug}, fixed version {identity.version}. Requires an MCP Apps-capable host for interaction.",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            annotations=ToolAnnotations(read_only_hint=True),
            meta={
                "ui": {
                    "resourceUri": identity.encode(),
                    "visibility": ["app", "model"],
                },
                GATEWAY_COMPONENT_META: "apps",
            },
        )
        tool._dispatch = dispatch
        return tool

    async def _callback(self, identity: AppResourceURI, slug: str, row: dict) -> Tool:
        binding = AppToolBinding(
            **{key: row[key] for key in ("alias", "server_name", "tool_name")}
        )
        resolved = await self.resolver.resolve(binding)
        if resolved.digest != row["digest"]:
            raise AppDomainError("App 工具来源或 Schema 已变化")

        async def dispatch(arguments: dict) -> ToolResult:
            await self.registry.load(identity.encode())
            result = await call_app_tool(
                self.registry.database,
                self.resolver,
                slug=slug,
                version=identity.version,
                alias=binding.alias,
                arguments=arguments,
            )
            self.registry.check()
            return ToolResult(
                content=result.content,
                structured_content=result.structured_content,
                meta=result.meta,
                is_error=result.is_error,
            )

        original = resolved.tool
        tool = AppGatewayTool(
            name=app_callback_name(identity.app_id, identity.version, binding.alias),
            title=binding.alias,
            description=f"App {slug} callback: {binding.alias}",
            parameters=original.parameters,
            output_schema=original.output_schema,
            annotations=original.annotations,
            meta={
                **original.get_meta(),
                "ui": {"visibility": ["app"]},
                GATEWAY_COMPONENT_META: "apps",
            },
        )
        tool._dispatch = dispatch
        return tool

    async def _get_tool(self, name: str, version=None) -> Tool | None:
        """先匹配明确名称，再查询对应来源；不存在名称不遍历上游。"""
        try:
            for identity in self.registry.published():
                with_version = identity.encode()
                if name == app_open_name(identity):
                    _, slug, info, _ = await self.registry.load(with_version)
                    if info["content_format"] in {"prefab", "html"}:
                        return self._open(identity, slug)
                else:
                    # 先读同实例发布记录，别名不通过任意 name 反向猜源。
                    from app.storage.app_models import AppRecord
                    from app.storage.unit_of_work import UnitOfWork

                    with UnitOfWork(self.registry.database) as unit:
                        slug = unit.session.get(AppRecord, identity.app_id).slug
                    for row in app_binding_rows(
                        self.registry.database, slug, identity.version
                    ):
                        if name == app_callback_name(
                            identity.app_id, identity.version, row["alias"]
                        ):
                            await self.registry.load(with_version)
                            return await self._callback(identity, slug, row)
        except (ValueError, OSError):
            return None
        return None

    async def _list_tools(self) -> Sequence[Tool]:
        """UI-only 后端仍有受众标记，Catalog/CodeMode 可据此过滤。"""
        try:
            entries = self.registry.published()
        except ValueError:
            return []
        result = []
        for identity in entries:
            try:
                _, slug, info, _ = await self.registry.load(identity.encode())
                if info["content_format"] not in {"prefab", "html"}:
                    continue
                result.append(self._open(identity, slug))
                for row in app_binding_rows(
                    self.registry.database, slug, identity.version
                ):
                    result.append(await self._callback(identity, slug, row))
            except (ValueError, OSError):
                continue
        try:
            self.registry.check()
        except ValueError:
            return []
        return result
