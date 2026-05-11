import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.workbench_service import get_run_detail, list_run_recommendations, list_runs, run_workbench
from app.services.market_data import MarketDataError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.post("")
def create_run(payload: dict, db: Session = Depends(get_db)):
    try:
        return run_workbench(db, payload)
    except (ValueError, MarketDataError, RuntimeError) as exc:
        logger.exception("create_run failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("create_run unexpected error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("")
def get_runs(
    run_mode: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    if run_mode is not None and run_mode not in {"formal", "experiment"}:
        raise HTTPException(status_code=400, detail="run_mode must be formal or experiment")
    return list_runs(db, run_mode=run_mode, limit=limit)


@router.get("/{run_id}")
def detail(run_id: str, db: Session = Depends(get_db)):
    run = get_run_detail(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.get("/{run_id}/recommendations")
def recommendations(run_id: str, db: Session = Depends(get_db)):
    run = get_run_detail(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return list_run_recommendations(db, run_id)
