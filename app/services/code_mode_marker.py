"""公开元工具标记，代理回网关时也不能递归纳入 CodeMode。"""

from collections.abc import Sequence

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools import Tool
from fastmcp.utilities.versions import VersionSpec

from app.services.code_mode_provider import GATEWAY_COMPONENT_META

META_TOOLS = frozenset({"search", "get_schema", "execute"})


class CodeModeMarker(Transform):
    """list/get 都保留标记，不只依赖客户端目录缓存。"""

    def _mark(self, tool: Tool) -> Tool:
        if tool.name not in META_TOOLS:
            return tool
        return tool.model_copy(
            update={"meta": {**(tool.meta or {}), GATEWAY_COMPONENT_META: "code-mode"}}
        )

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """只标记经过官方 CodeMode Transform 生成的三个元工具。"""
        return [self._mark(tool) for tool in tools]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        """现代直接查询工具也得到同一标记。"""
        tool = await call_next(name, version=version)
        return self._mark(tool) if tool else None
