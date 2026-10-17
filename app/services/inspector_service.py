import asyncio
import hashlib
import logging
import re
import uuid
from collections.abc import Generator
from datetime import datetime
from typing import Any, Dict, List, Optional

import anyio
import httpx2
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.client.extension import advertise

from app.services.inspector_skills import EXTENSION, capabilities
from app.services.inspector_runs import InspectorRuns
from app.services.inspector_events import InspectorEvents
from app.services.inspector_client_options import InspectorClientOptions
from app.services.security_service import security_service

logger = logging.getLogger(__name__)


class McpcatAuth(httpx2.Auth):
    """使用配置的认证头访问本实例，不混用上游 OAuth。"""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def auth_flow(
        self, request: httpx2.Request
    ) -> Generator[httpx2.Request, None, None]:
        """向本实例请求注入当前配置的 Key header。"""
        request.headers[security_service.get_auth_header_name()] = self.api_key
        yield request


class InspectorSession:
    def __init__(self, server_name: str, client: Client, api_key: str | None = None):
        self.id = str(uuid.uuid4())
        self.server_name = server_name
        self.client = client
        self.created_at = datetime.now()
        self.last_used = datetime.now()
        self.owner = hashlib.sha256((api_key or "").encode()).digest()
        self.lock = asyncio.Lock()
        self.entries: dict[tuple[str, str], dict] = {}
        self.records: list[dict] = []
        self.runs = InspectorRuns()
        self.events = InspectorEvents()
        self.subscription_authorized = lambda: bool(
            api_key and security_service.verify_api_key(api_key)
        )


class InspectorService:
    def __init__(self):
        self.sessions: Dict[str, InspectorSession] = {}
        self._cleanup_task = None

    async def create_session(
        self, server_name: str, base_url: str, api_key: Optional[str] = None,
        *, client_options: InspectorClientOptions | None = None,
    ) -> str:
        # Construct internal MCP URL
        # Note: we use 127.0.0.1 to avoid external networking issues
        if not re.fullmatch(r"[\w.-]+", server_name):
            raise ValueError("无效 MCP 端点名称")
        if len(self.sessions) >= 100:
            raise ValueError("测试会话已达上限，请关闭不用的会话")
        mcp_url = f"{base_url}/mcp/{server_name}"

        auth = McpcatAuth(api_key) if api_key else None
        runs = InspectorRuns()
        events = InspectorEvents()

        sampling_options = {}
        if client_options is not None and client_options.sampling:
            from mcp.types import SamplingCapability

            sampling_options = {
                "sampling_handler": runs.sample,
                "sampling_capabilities": SamplingCapability(tools={}, context={}),
            }

        client = Client(
            StreamableHttpTransport(mcp_url),
            auth=auth,
            elicitation_handler=runs.elicit,
            message_handler=events,
            progress_handler=events.progress,
            log_handler=events.log,
            extensions=[
                advertise(EXTENSION),
                advertise(
                    "io.modelcontextprotocol/ui",
                    {"mimeTypes": ["text/html;profile=mcp-app"]},
                ),
            ],
            timeout=30,
            **sampling_options,
            **(client_options or InspectorClientOptions()).client_kwargs(),
        )
        session = InspectorSession(server_name, client, api_key)
        session.runs = runs
        session.events = events

        # Test connection
        try:
            with anyio.fail_after(25):
                async with client:
                    pass  # SDK 协商已完成，纯 Skills 端点不要求 tools。
        except Exception as e:
            logger.warning("Inspector connection failed: %s", type(e).__name__)
            raise ValueError("无法连接到服务器，请检查端点状态与权限") from e

        self.sessions[session.id] = session
        return session.id

    async def get_tools(self, session_id: str) -> List[Dict[str, Any]]:
        session = self._get_session(session_id)
        async with session.client:
            if "tools" not in capabilities(session.client)["capabilities"]:
                return []
            tools = await session.client.list_tools()
            result = []
            for tool in tools:
                schema = self._extract_tool_schema(tool)
                result.append(
                    {
                        "name": getattr(tool, "name", ""),
                        "description": getattr(tool, "description", None)
                        or getattr(tool, "title", ""),
                        "input_schema": schema,
                    }
                )
            return result

    def authorize(self, session_id: str, api_key: str | None) -> InspectorSession:
        """HTTP 层已验证当前凭证；禁止跨身份借用旧会话客户端。"""
        session = self._get_session(session_id)
        if session.owner != hashlib.sha256((api_key or "").encode()).digest():
            raise PermissionError("会话不存在或不属于当前身份")
        return session

    async def call_tool(
        self, session_id: str, tool_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        session = self._get_session(session_id)
        async with session.client:
            result = await session.client.call_tool(tool_name, arguments)

            # result is a CallToolResult
            # It has .data (structured) and .content (unstructured blocks)
            content_blocks = []
            for block in result.content:
                block_any: Any = block
                text = getattr(block_any, "text", None)
                data = getattr(block_any, "data", None)
                if text is not None:
                    content_blocks.append({"type": "text", "text": text})
                elif data is not None:
                    content_blocks.append({"type": "data", "data": data})
                else:
                    content_blocks.append({"type": "unknown", "raw": str(block)})

            return {
                "data": result.data,
                "content": content_blocks,
                "is_error": result.is_error if hasattr(result, "is_error") else False,
                "structured_content": getattr(result, "structured_content", None),
                "protocol_content": [
                    block.model_dump(mode="json", by_alias=True, exclude_none=True)
                    for block in result.content
                ],
            }

    async def close_session(self, session_id: str):
        if session_id in self.sessions:
            session = self.sessions.pop(session_id)
            await session.runs.close()
            await session.events.close()

    def _get_session(self, session_id: str) -> InspectorSession:
        if session_id not in self.sessions:
            raise Exception("会话不存在或已过期")
        session = self.sessions[session_id]
        session.last_used = datetime.now()
        return session

    def _extract_tool_schema(self, tool: Any) -> Dict[str, Any]:
        # Try common attribute names across FastMCP versions
        parameters = getattr(tool, "parameters", None)
        if parameters is None:
            parameters = getattr(tool, "input_schema", None)
        if parameters is None:
            parameters = getattr(tool, "inputSchema", None)
        if parameters is None:
            parameters = getattr(tool, "schema", None)

        if parameters is None:
            try:
                dump = tool.model_dump() if hasattr(tool, "model_dump") else {}
                parameters = (
                    dump.get("parameters")
                    or dump.get("input_schema")
                    or dump.get("inputSchema")
                    or dump.get("schema")
                )
            except Exception:
                parameters = None

        model_dump = getattr(parameters, "model_dump", None)
        if callable(model_dump):
            dumped: Dict[str, Any] = model_dump() or {}
            if isinstance(dumped, dict):
                return dumped
            return {}

        if isinstance(parameters, dict):
            return parameters

        return {}

    def start_cleanup_task(self):
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self):
        while True:
            await asyncio.sleep(60)
            now = datetime.now()
            to_delete = []
            for sid, session in self.sessions.items():
                # Timeout after 30 minutes of inactivity
                if (now - session.last_used).total_seconds() > 1800:
                    to_delete.append(sid)

            for sid in to_delete:
                logger.info(f"Cleaning up inactive inspector session: {sid}")
                await self.close_session(sid)


# Singleton
inspector_service = InspectorService()
