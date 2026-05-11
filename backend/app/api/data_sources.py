from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.data_sources import get_local_pool_status, list_data_sources, set_data_source_enabled
from app.services.market_pool import refresh_quote_pool


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
    if source_type not in {"quote", "kline"}:
        raise HTTPException(status_code=400, detail="source_type must be quote or kline")
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
