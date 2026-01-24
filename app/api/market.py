from fastapi import APIRouter, Request

router = APIRouter()

def _get_market_service(request: Request):
    if not hasattr(request.app.state, 'market_service'):
        from app.services.market_service import MarketService, MARKET_DATA_URL_PRIMARY, MARKET_DATA_URL_FALLBACK, MARKET_DATA_TTL
        from pathlib import Path
        return MarketService(
            remote_url=MARKET_DATA_URL_PRIMARY,
            remote_url_fallback=MARKET_DATA_URL_FALLBACK,
            ttl_seconds=MARKET_DATA_TTL,
            local_path=Path(__file__).resolve().parents[2] / "data" / "mcp_market.json"
        )
    return request.app.state.market_service

@router.get("/servers")
async def get_market_servers(request: Request):
    market_service = _get_market_service(request)
    return await market_service.get_market()
