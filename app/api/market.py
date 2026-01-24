import json
from pathlib import Path
from fastapi import APIRouter, HTTPException

router = APIRouter()

@router.get("/servers")
async def get_market_servers():
    data_path = Path(__file__).resolve().parents[2] / "data" / "mcp_market.json"
    if not data_path.exists():
        raise HTTPException(status_code=404, detail="市场数据不存在")

    try:
        data = json.loads(data_path.read_text())
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"市场数据读取失败: {e}")
