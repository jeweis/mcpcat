import asyncio
import logging
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional
import httpx
from fastmcp import Client

logger = logging.getLogger(__name__)

class McpcatAuth(httpx.Auth):
    def __init__(self, api_key: str):
        self.api_key = api_key

    def auth_flow(self, request):
        request.headers["Mcpcat-Key"] = self.api_key
        yield request

class InspectorSession:
    def __init__(self, server_name: str, client: Client):
        self.id = str(uuid.uuid4())
        self.server_name = server_name
        self.client = client
        self.created_at = datetime.now()
        self.last_used = datetime.now()

class InspectorService:
    def __init__(self):
        self.sessions: Dict[str, InspectorSession] = {}
        self._cleanup_task = None

    async def create_session(self, server_name: str, base_url: str, api_key: Optional[str] = None) -> str:
        # Construct internal MCP URL
        # Note: we use 127.0.0.1 to avoid external networking issues
        mcp_url = f"{base_url}/mcp/{server_name}"
        
        auth = McpcatAuth(api_key) if api_key else None
            
        client = Client(mcp_url, auth=auth)
        session = InspectorSession(server_name, client)
        
        # Test connection
        try:
            async with client:
                await client.list_tools()
        except Exception as e:
            logger.error(f"Failed to connect to MCP server {server_name}: {e}")
            raise Exception(f"无法连接到服务器: {str(e)}")
            
        self.sessions[session.id] = session
        return session.id

    async def get_tools(self, session_id: str) -> List[Dict[str, Any]]:
        session = self._get_session(session_id)
        async with session.client:
            tools = await session.client.list_tools()
            result = []
            for tool in tools:
                schema = self._extract_tool_schema(tool)
                result.append({
                    "name": getattr(tool, "name", ""),
                    "description": getattr(tool, "description", None) or getattr(tool, "title", ""),
                    "input_schema": schema,
                })
            return result

    async def call_tool(self, session_id: str, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
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
                "is_error": result.is_error if hasattr(result, "is_error") else False
            }

    async def close_session(self, session_id: str):
        if session_id in self.sessions:
            del self.sessions[session_id]

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
                del self.sessions[sid]

# Singleton
inspector_service = InspectorService()
