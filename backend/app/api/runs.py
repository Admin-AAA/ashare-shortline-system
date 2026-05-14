import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.market_data import MarketDataError
from app.services.workbench_service import (
    delete_run,
    enqueue_workbench_run,
    get_run_detail,
    list_run_recommendations,
    list_runs,
    run_workbench,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.post("")
async def create_run(payload: dict, db: Session = Depends(get_db)):
    try:
        if not payload.get("preset_id") and not payload.get("single_skill_id"):
            raise ValueError("preset_id or single_skill_id is required")
        if not payload.get("wait_for_completion") and payload.get("async", True) is not False:
            return enqueue_workbench_run(db, payload)
        return await run_workbench(db, payload)
    except (ValueError, MarketDataError, RuntimeError) as exc:
        logger.exception("create_run failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("create_run unexpected error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/backtest")
def create_backtest(payload: dict, db: Session = Depends(get_db)):
    """在历史日期区间上执行回测。

    请求体:
        start_date: str  # 起始日期 YYYY-MM-DD
        end_date: str    # 结束日期 YYYY-MM-DD
        run_mode: str    # formal | experiment（默认 experiment）
        horizon_days: int # 持仓天数（默认 5）
    """
    from app.services.backtest_service import run_backtest

    start_date = payload.get("start_date")
    end_date = payload.get("end_date")
    if not start_date or not end_date:
        raise HTTPException(status_code=400, detail="start_date and end_date are required")
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")

    run_mode = payload.get("run_mode", "experiment")
    if run_mode not in {"formal", "experiment"}:
        raise HTTPException(status_code=400, detail="run_mode must be formal or experiment")

    horizon_days = int(payload.get("horizon_days", 5))
    if horizon_days < 1 or horizon_days > 20:
        raise HTTPException(status_code=400, detail="horizon_days must be between 1 and 20")

    try:
        return run_backtest(db, start_date, end_date, run_mode, horizon_days)
    except Exception as exc:
        logger.exception("backtest failed")
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


@router.delete("/{run_id}")
def delete(run_id: str, db: Session = Depends(get_db)):
    ok = delete_run(db, run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="run not found")
    return {"deleted": run_id}


@router.get("/{run_id}/recommendations")
def recommendations(run_id: str, db: Session = Depends(get_db)):
    run = get_run_detail(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return list_run_recommendations(db, run_id)
