from fastapi import APIRouter, Request, HTTPException, Body
from typing import Dict, Any, Optional
from pydantic import BaseModel
from app.services.inspector_service import inspector_service

router = APIRouter()

class CreateSessionRequest(BaseModel):
    server_name: str

class CallToolRequest(BaseModel):
    tool: str
    arguments: Dict[str, Any] = {}

@router.post("/sessions")
async def create_session(req: CreateSessionRequest, request: Request):
    # Determine base URL for internal connection
    # Prefer localhost to avoid external network issues
    port = request.app.state.port if hasattr(request.app.state, 'port') else 8000
    base_url = f"http://127.0.0.1:{port}"
    
    # Get API key from current request to proxy authentication
    api_key = request.headers.get("Mcpcat-Key")
    
    try:
        session_id = await inspector_service.create_session(
            req.server_name, 
            base_url,
            api_key=api_key
        )
        return {"session_id": session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sessions/{session_id}/tools")
async def get_tools(session_id: str):
    try:
        tools = await inspector_service.get_tools(session_id)
        return {"tools": tools}
    except Exception as e:
        detail = str(e)
        if "会话不存在" in detail:
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=500, detail=detail)

@router.post("/sessions/{session_id}/call")
async def call_tool(session_id: str, req: CallToolRequest):
    try:
        result = await inspector_service.call_tool(session_id, req.tool, req.arguments)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/sessions/{session_id}")
async def close_session(session_id: str):
    await inspector_service.close_session(session_id)
    return {"status": "ok"}
