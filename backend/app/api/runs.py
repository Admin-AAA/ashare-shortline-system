import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.engine.strategy_backtest_adapter import run_strategy_backtest_adapter
from app.services.market_data import MarketDataError
from app.services.workbench_service import (
    delete_run,
    enqueue_workbench_run,
    get_run_detail,
    get_run_filter_audit,
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


@router.post("/backtest/comparison")
def create_backtest_comparison(payload: dict, db: Session = Depends(get_db)):
    """执行 baseline vs candidate 双版本回测对比（含 walk-forward + time-split）。

    请求体:
        start_date: str
        end_date: str
        run_mode: str (默认 experiment)
        horizon_days: int (默认 5)
        baseline_score_line: float (默认 72.0)
        candidate_score_line: float (默认 65.0)
    """
    from app.services.backtest_service import run_backtest_comparison

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
    baseline_score_line = float(payload.get("baseline_score_line", 72.0))
    candidate_score_line = float(payload.get("candidate_score_line", 65.0))
    try:
        return run_backtest_comparison(
            db, start_date, end_date, run_mode, horizon_days,
            baseline_score_line=baseline_score_line,
            candidate_score_line=candidate_score_line,
        )
    except Exception as exc:
        logger.exception("backtest comparison failed")
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
    score_line = float(payload.get("score_line", 72.0))
    strategy_id = str(payload.get("strategy_id") or "").strip()
    skill_id = str(payload.get("skill_id") or "").strip() or None
    try:
        action = str(payload.get("action") or "").strip()
        adapter_result = run_strategy_backtest_adapter(
            db,
            strategy_id=strategy_id,
            action=action,
            start_date=start_date,
            end_date=end_date,
        )
        if adapter_result is not None:
            return adapter_result
        if action:
            raise ValueError(f"strategy backtest action not configured: {strategy_id or 'default'}.{action}")
        return run_backtest(
            db,
            start_date,
            end_date,
            run_mode,
            horizon_days,
            score_line,
            skill_id=skill_id or strategy_id or None,
        )
    except ValueError as exc:
        logger.exception("backtest request rejected")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("backtest failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/backtests")
def get_backtests(
    skill_id: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
    db: Session = Depends(get_db),
):
    from app.services.backtest_history_service import list_backtest_runs

    return list_backtest_runs(db, skill_id=skill_id, limit=limit)


@router.get("/backtests/version-matrix")
def get_backtest_version_matrix(
    skill_id: str | None = Query(default=None),
    limit: int = Query(default=40, ge=1, le=200),
    db: Session = Depends(get_db),
):
    from app.services.backtest_history_service import get_backtest_version_matrix

    return get_backtest_version_matrix(db, skill_id=skill_id, limit=limit)


@router.get("/backtests/{backtest_id}")
def backtest_detail(backtest_id: str, db: Session = Depends(get_db)):
    from app.services.backtest_history_service import get_backtest_run

    run = get_backtest_run(db, backtest_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    return run


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


@router.post("/{run_id}/resume")
async def resume(run_id: str, db: Session = Depends(get_db)):
    """从中断点继续执行 Run。"""
    run = get_run_detail(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run["status"] not in ("interrupted", "failed"):
        raise HTTPException(status_code=400, detail=f"run status '{run['status']}' is not resumable")
    params = run.get("params", {})
    if not params:
        raise HTTPException(status_code=400, detail="run has no saved params to resume from")
    try:
        return await run_workbench(db, params, existing_run_id=run_id)
    except (ValueError, MarketDataError, RuntimeError) as exc:
        logger.exception("resume run failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("resume run unexpected error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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


@router.get("/{run_id}/funnel")
def funnel(run_id: str, db: Session = Depends(get_db)):
    audit = get_run_filter_audit(db, run_id)
    if audit is None:
        raise HTTPException(status_code=404, detail="run not found")
    return audit
