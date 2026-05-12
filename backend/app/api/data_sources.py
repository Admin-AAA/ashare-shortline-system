from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.data_sources import get_local_pool_status, list_data_sources, set_data_source_enabled
from app.services.market_pool import (
    refresh_capital_flows_for_codes,
    refresh_concept_sectors,
    refresh_dragon_tiger,
    refresh_limit_up_board,
    refresh_news_for_codes,
    refresh_quote_pool,
)


router = APIRouter(prefix="/api/data-sources", tags=["data-sources"])


@router.get("")
def list_sources(db: Session = Depends(get_db)):
    return list_data_sources(db)


@router.get("/pool")
def local_pool(
    db: Session = Depends(get_db),
    u_limit: int = 50,
    u_offset: int = 0,
    b_limit: int = 50,
    b_offset: int = 0,
):
    return get_local_pool_status(
        db,
        unified_limit=u_limit,
        unified_offset=u_offset,
        bars_limit=b_limit,
        bars_offset=b_offset,
    )


@router.post("/pool/refresh")
def refresh_pool(db: Session = Depends(get_db)):
    quotes, meta = refresh_quote_pool(db)
    return {"refreshed": len(quotes), "meta": meta}


@router.post("/{source_type}/{source}/enabled")
def set_enabled(source_type: str, source: str, payload: dict, db: Session = Depends(get_db)):
    valid_types = {"quote", "kline", "limit_up", "concept_sector", "capital_flow", "dragon_tiger", "news"}
    if source_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"source_type must be one of {valid_types}")
    if "enabled" not in payload:
        raise HTTPException(status_code=400, detail="enabled is required")
    result = set_data_source_enabled(
        db,
        source=source,
        source_type=source_type,
        enabled=bool(payload.get("enabled")),
        note=payload.get("note"),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="data source not found")
    return result


@router.post("/pool/refresh/limit-up")
def refresh_limit_up(db: Session = Depends(get_db)):
    rows = refresh_limit_up_board(db)
    return {"refreshed": len(rows)}


@router.post("/pool/refresh/concept-sectors")
def refresh_sectors(db: Session = Depends(get_db)):
    rows = refresh_concept_sectors(db)
    return {"refreshed": len(rows)}


@router.post("/pool/refresh/dragon-tiger")
def refresh_dt(db: Session = Depends(get_db)):
    rows = refresh_dragon_tiger(db)
    return {"refreshed": len(rows)}
