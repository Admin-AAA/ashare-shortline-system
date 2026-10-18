import logging

from fastapi import Depends, HTTPException, Query
from app.api.contracts import ContractRouter as APIRouter
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.engine.strategy_backtest_adapter import run_strategy_backtest_adapter
from app.services.market_data import MarketDataError
from app.services.registry.default_strategy import DEFAULT_SINGLE_STRATEGY_ID
from app.services.runs.deletion_guard import DownstreamFactDeletionBlocked
from app.services.runs.recovery import RecoveryCapabilityError
from app.services.workbench_service import (
    delete_run,
    dispatch_workbench_run,
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
            # 同步 DB 写入放到线程池，避免阻塞事件循环（期间所有请求含 /api/auth/status 都会卡住）。
            from starlette.concurrency import run_in_threadpool

            return await run_in_threadpool(enqueue_workbench_run, db, payload)
        return await run_workbench(db, payload)
    except (ValueError, MarketDataError, RuntimeError) as exc:
        logger.exception("create_run failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("create_run unexpected error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/matrix")
def create_matrix_run(payload: dict | None = None):
    try:
        from app.services.scheduler_service import start_preset_matrix_now

        return start_preset_matrix_now(payload or {})
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("create matrix run failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/matrix/status")
def matrix_run_status():
    from app.services.scheduler_service import get_latest_preset_matrix_task_run

    return {"task_run": get_latest_preset_matrix_task_run()}


@router.post("/backtest/comparison")
def create_backtest_comparison(payload: dict, db: Session = Depends(get_db)):
    """执行 baseline vs candidate 双版本回测对比（含 walk-forward + time-split）。

    Uses the P0 Research Gateway Runner for both arms; writes only to
    ``backtest_*`` tables, never to ``btrec_*`` or ``Recommendation``.

    请求体:
        start_date: str
        end_date: str

        horizon_days: int (默认 5)
        baseline_score_line: float (默认 72.0)
        candidate_score_line: float (默认 65.0)
        strategy_id: str (默认炒股养家 chao-gu-yang-jia-skill)
    """
    from app.services.backtest.comparison import run_p0_comparison

    start_date = payload.get("start_date")
    end_date = payload.get("end_date")
    if not start_date or not end_date:
        raise HTTPException(status_code=400, detail="start_date and end_date are required")
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")
    horizon_days = int(payload.get("horizon_days", 5))
    baseline_score_line = float(payload.get("baseline_score_line", 72.0))
    candidate_score_line = float(payload.get("candidate_score_line", 65.0))
    strategy_id = str(payload.get("strategy_id") or payload.get("skill_id") or DEFAULT_SINGLE_STRATEGY_ID).strip()
    try:
        return run_p0_comparison(
            db, start_date, end_date, horizon_days,
            baseline_score_line=baseline_score_line,
            candidate_score_line=candidate_score_line,
            strategy_id=strategy_id,
        )
    except Exception as exc:
        logger.exception("backtest comparison failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/analyze-stock")
def analyze_single_stock(payload: dict, db: Session = Depends(get_db)):
    """单股票分析：以一只股票为输入，执行正式推荐管线的完整评分阶段。

    请求体:
        code: str           # 股票代码（必填）
        trade_date: str     # 分析日期 YYYY-MM-DD（默认最近交易日）
        include_llm: bool   # 是否允许正式管线触发 LLM skill 深审（会消耗 LLM 额度）
        skill_id: str       # 指定 skill（默认炒股养家 chao-gu-yang-jia-skill）
    """
    from app.services.single_stock_analysis import analyze_stock

    code = str(payload.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="code is required")

    trade_date = str(payload.get("trade_date") or "").strip() or None
    include_llm = bool(payload.get("include_llm", False))
    skill_id = str(payload.get("skill_id") or DEFAULT_SINGLE_STRATEGY_ID).strip()
    preset_id = str(payload.get("preset_id") or "").strip() or None

    try:
        return analyze_stock(
            db, code, trade_date=trade_date, include_llm=include_llm,
            skill_id=skill_id, preset_id=preset_id,
        )
    except Exception as exc:
        logger.exception("single stock analysis failed for %s", code)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/backtest")
def create_backtest(payload: dict, db: Session = Depends(get_db)):
    """Compatibility adapter for the immutable P0 single-strategy Runner.

    This endpoint retains its legacy URL and request fields, but every ordinary
    single-strategy request now creates/reuses an isolated ``backtest_*``
    attempt.  It cannot fall through to ``run_backtest`` and therefore cannot
    delete or relabel legacy Recommendation/RecommendationOutcome evidence.
    Strategy-declared named actions remain on their dedicated action adapters.
    """
    from app.engine.backtest import BacktestRequest, DateRange, SingleDate
    from app.engine.execution_policy import (
        DEFAULT_EXECUTION_POLICY,
        execution_policy_from_config_projection,
    )
    from app.services.backtest.research_repository import ResearchBacktestRepository
    from app.services.backtest.research_runner import ResearchBacktestRunner

    start_date = str(payload.get("start_date") or "").strip()
    end_date = str(payload.get("end_date") or "").strip()
    if not start_date or not end_date:
        raise HTTPException(status_code=400, detail="start_date and end_date are required")
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")

    try:
        horizon_days = int(payload.get("horizon_days", 5))
        if horizon_days < 1 or horizon_days > 20:
            raise ValueError("horizon_days must be between 1 and 20")
        score_line = float(payload.get("score_line", 72.0))
        strategy_id = str(payload.get("strategy_id") or payload.get("skill_id") or "").strip()
        action = str(payload.get("action") or "").strip()
        if action:
            adapter_result = run_strategy_backtest_adapter(
                db,
                strategy_id=strategy_id,
                action=action,
                start_date=start_date,
                end_date=end_date,
            )
            if adapter_result is not None:
                return adapter_result
            raise ValueError(f"strategy backtest action not configured: {strategy_id or 'default'}.{action}")
        if not strategy_id:
            raise ValueError(
                "strategy_id (or skill_id) is required for the P0 research backtest; "
                "multi-strategy legacy backtests are no longer accepted at this endpoint"
            )

        raw_codes = payload.get("target_codes")
        if raw_codes is None:
            target_codes: tuple[str, ...] = ()
        elif not isinstance(raw_codes, list):
            raise ValueError("target_codes must be an array of stock codes")
        else:
            target_codes = tuple(str(code).strip() for code in raw_codes)
        raw_policy = payload.get("execution_policy") or {}
        if not isinstance(raw_policy, dict):
            raise ValueError("execution_policy must be an object when supplied")
        policy_config = dict(DEFAULT_EXECUTION_POLICY.config_projection())
        policy_config.update(raw_policy)
        if "horizon_days" not in raw_policy:
            policy_config["horizon_days"] = horizon_days
        request = BacktestRequest(
            strategy_id=strategy_id,
            date_selector=SingleDate(start_date) if start_date == end_date else DateRange(start_date, end_date),
            execution_policy=execution_policy_from_config_projection(policy_config),
            target_codes=target_codes,
            parameter_set={"minimum_signal_score": score_line},
        )
        result = ResearchBacktestRunner(ResearchBacktestRepository(db)).run(request)
        result["compatibility_adapter"] = {
            "source_endpoint": "/api/runs/backtest",
            "runner": "p0_research",
            "legacy_evidence_mutation": False,
        }
        return result
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
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=200),
    status: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    query: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    from app.services.backtest_history_service import list_backtest_runs_page

    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from must be <= date_to")
    return list_backtest_runs_page(
        db,
        skill_id=skill_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
        query=query,
        page=page,
        page_size=page_size or limit,
    )


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
    limit: int = Query(default=100, ge=1, le=500),
    run_mode: str | None = Query(default=None, pattern="^(formal|experiment|backtest)$"),
    fields: str | None = Query(default=None, pattern="^(full|slim)$", description="slim：去掉体积最大的 result_summary（默认 full，返回不变）"),
    db: Session = Depends(get_db),
):
    runs = list_runs(db, limit=limit, run_mode=run_mode)
    if fields == "slim":
        from app.services.records_service import slim_run

        runs = [slim_run(r) for r in runs]
    return runs


@router.get("/{run_id}")
def detail(run_id: str, db: Session = Depends(get_db)):
    run = get_run_detail(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.post("/{run_id}/resume")
async def resume(run_id: str, db: Session = Depends(get_db)):
    """Dispatch completion of one database-verified persistence collection."""
    run = get_run_detail(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if not bool(run.get("can_resume")):
        raise HTTPException(
            status_code=409,
            detail={
                "code": str(run.get("resume_block_code") or "resume_rejected"),
                "message": str(
                    run.get("resume_block_reason") or "运行不满足恢复条件"
                ),
            },
        )
    try:
        dispatch_workbench_run(db, run_id)
    except RecoveryCapabilityError as exc:
        raise HTTPException(status_code=409, detail=exc.detail()) from exc
    except (ValueError, RuntimeError) as exc:
        logger.exception("resume run dispatch failed")
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "run": get_run_detail(db, run_id),
        "recommendations": [],
        "queued": True,
    }


@router.delete("/{run_id}")
def delete(run_id: str, db: Session = Depends(get_db)):
    try:
        ok = delete_run(db, run_id)
    except DownstreamFactDeletionBlocked as exc:
        raise HTTPException(status_code=409, detail=exc.detail()) from exc
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
