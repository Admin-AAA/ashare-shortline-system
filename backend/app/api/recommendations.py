import logging

from fastapi import Depends, HTTPException, Query
from app.api.contracts import ContractRouter as APIRouter
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session  # noqa: F401 (Query used in endpoints)

from app.db.session import get_db
from app.engine.trading_costs import ADDITIONAL_COST_PCT, is_net_profitable
from app.services.outcome_service import recent_formal_win_rate
from app.services.workbench_service import (
    delete_recommendation,
    get_recommendation_detail,
    get_run_replay_analysis,
    list_recommendation_history,
    save_review_note,
    set_watch_status,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


class ExecutionConfirmationsRequest(BaseModel):
    """批量实时确认的严格请求契约。"""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    recommendation_ids: list[str]
    refresh: bool = False

    @field_validator("recommendation_ids")
    @classmethod
    def normalize_recommendation_ids(cls, raw_ids: list[str]) -> list[str]:
        from app.services.batch_execution_confirmation import MAX_BATCH_IDS

        ids = list(dict.fromkeys(item.strip() for item in raw_ids if item.strip()))
        if not ids:
            raise ValueError("recommendation_ids must contain 1-50 non-empty ids")
        if len(ids) > MAX_BATCH_IDS:
            raise ValueError(f"recommendation_ids exceeds maximum of {MAX_BATCH_IDS}")
        return ids


@router.get("/history")
def history(
    code: str | None = Query(default=None),
    label: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    bucket: str | None = Query(default=None),
    setup_type: str | None = Query(default=None),
    strategy_id: str | None = Query(default=None, description="按策略下钻：仅返回 skill_breakdown 含该 strategy_id 的推荐"),
    strategy_version_id: str | None = Query(default=None, description="按策略版本下钻：匹配运行时 selected_skills.version_id"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """跨 run 推荐历史（带 outcome），支持分页 + 筛选。

    填补推荐历史记录页缺口：用户查看所有推荐过的股票及其实际表现。
    strategy_id 支持从策略清单页下钻到单策略的全部推荐历史（北极星 C 项）。
    """
    if label is not None and label not in {"success", "failed", "missed", "pending"}:
        raise HTTPException(status_code=400, detail="label must be success/failed/missed/pending")
    if strategy_version_id and not strategy_id:
        raise HTTPException(status_code=400, detail="strategy_version_id requires strategy_id")
    return list_recommendation_history(
        db,
        code=code,
        label=label,
        date_from=date_from,
        date_to=date_to,
        bucket=bucket,
        setup_type=setup_type,
        strategy_id=strategy_id,
        strategy_version_id=strategy_version_id,
        limit=limit,
        offset=offset,
    )


@router.get("/realtime-pnl")
def realtime_pnl(
    refresh: bool = Query(default=False, description="true 跳过 20s 缓存强制重新拉取 rt_k"),
    db: Session = Depends(get_db),
):
    """未最终结算（pending / provisional）推荐的实盘实时盈亏。

    盘中推荐按生成时的实时推荐价入场；盘后推荐等待下一交易日后，再复用 outcome
    结算口径判定入场价并以 rt_k 现价算浮盈。纯展示，不落库、不改真实结算。
    """
    from app.services.realtime_pnl_service import list_pending_realtime_pnl

    return list_pending_realtime_pnl(db, use_cache=not refresh)


@router.post("/execution-confirmations")
def execution_confirmations(payload: ExecutionConfirmationsRequest, db: Session = Depends(get_db)):
    """未结算正式推荐的批量次日买入确认（历史页当前页一次性实时确认）。

    只对当前页未结算 formal 推荐发起一次批量确认：后端重新校验未结算资格，一次批量
    rt_k + 一次公共市场上下文 + 一次公共特征准备，按各自原策略重跑确定性规则，返回
    允许执行 / 继续观察 / 取消买入 / 行情未就绪 / 信号已过期。不调用 LLM/Agent；只
    持久化独立的执行确认状态，不改结算。已结算 ID 不进入 items，只计入
    settled_skipped_count。
    """
    from app.services.batch_execution_confirmation import confirm_batch_executions

    try:
        return confirm_batch_executions(db, payload.recommendation_ids, refresh=payload.refresh)
    except Exception as exc:  # noqa: BLE001 - 批次公共源失败已在服务层封装为 errors
        logger.exception("批量实时确认发生未处理异常")
        raise HTTPException(status_code=500, detail="批量实时确认失败") from exc


@router.get("/outcome-summary")
def outcome_summary(
    codes: list[str] = Query(default=[], description="股票代码列表，支持逗号分隔或重复参数"),
    db: Session = Depends(get_db),
):
    """每只股票已结算推荐的历史表现（真实盈亏，非回测模拟）。

    填补实时候选卡「历史验证」维度：展开候选卡时展示该股票此前被推荐后的真实胜负与均收益，
    替换前端 ConfidenceMeter 的 score_gap/20 启发式。仅统计实盘推荐（recommendation_id 以 rec_ 开头），
    回测 btrec_* 为模拟数据，不计入「跑出来结果如何」。
    """
    from app.models.tables import Recommendation, RecommendationOutcome, RecommendationRun

    # 规范化：支持逗号分隔与重复参数
    normalized: list[str] = []
    for raw in codes:
        for part in str(raw).split(","):
            part = part.strip()
            if part:
                normalized.append(part)
    if not normalized:
        return {}

    rows = (
        db.query(
            Recommendation.code,
            Recommendation.trade_date,
            Recommendation.recommendation_id,
            RecommendationOutcome.executed_return,
            RecommendationOutcome.close_return,
        )
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .join(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
        .filter(Recommendation.code.in_(normalized))
        .filter(Recommendation.recommendation_id.like("rec_%"))
        .filter(RecommendationRun.run_mode == "formal")
        .filter(RecommendationRun.status == "completed")
        .filter(RecommendationOutcome.outcome_label.in_(["success", "failed"]))
        .filter(RecommendationOutcome.entry_triggered == True)  # noqa: E712
        .order_by(
            Recommendation.code,
            Recommendation.trade_date,
            Recommendation.final_score.desc(),
            Recommendation.recommendation_id,
        )
        .all()
    )

    returns_by_code: dict[str, list[float]] = {}
    seen: set[tuple[str, str]] = set()
    for code, trade_date, _recommendation_id, executed_return, close_return in rows:
        key = (str(code), str(trade_date))
        if key in seen:
            continue
        seen.add(key)
        gross_return = float(executed_return if executed_return is not None else close_return or 0)
        returns_by_code.setdefault(str(code), []).append(gross_return)

    result: dict[str, dict] = {}
    for code, returns in returns_by_code.items():
        settled = len(returns)
        wins = sum(is_net_profitable(value) for value in returns)
        result[code] = {
            "settled": settled,
            "wins": wins,
            "win_rate": round(wins * 100.0 / settled, 1) if settled else 0.0,
            "avg_return": round(
                sum(value - ADDITIONAL_COST_PCT for value in returns) / settled, 2,
            ) if settled else 0.0,
        }
    # 补齐「请求了但无历史」的代码，前端据此区分「无历史」与「未查询」
    for code in normalized:
        result.setdefault(code, {"settled": 0, "wins": 0, "win_rate": 0.0, "avg_return": 0.0})
    return result


@router.get("/win-rate")
def win_rate(
    window_days: int = Query(default=5, ge=1, le=60, description="近 N 个有已结算结果的 formal 交易日"),
    db: Session = Depends(get_db),
):
    """近 N 个已结算交易日的 formal 推荐聚合胜率/均收益。

    只统计实盘 rec_ 推荐中 success/failed outcome；零推荐日与只有 pending outcome 的日期
    既不进入分母，也不占用窗口名额。
    """
    return recent_formal_win_rate(db, window_trade_days=window_days)


@router.get("/runs/{run_id}/replay-analysis")
def replay_analysis(run_id: str, db: Session = Depends(get_db)):
    result = get_run_replay_analysis(db, run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="run not found")
    return result


@router.get("/{recommendation_id}")
def detail(recommendation_id: str, db: Session = Depends(get_db)):
    rec = get_recommendation_detail(db, recommendation_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    return rec


@router.post("/{recommendation_id}/execution-confirmation")
def execution_confirmation(recommendation_id: str, payload: dict | None = None, db: Session = Depends(get_db)):
    """用当前市场快照重跑原推荐主策略，分别给出未入场与已持仓建议。"""
    from app.services.execution_confirmation import confirm_recommendation_execution

    try:
        result = confirm_recommendation_execution(
            db,
            recommendation_id,
            include_agent=bool((payload or {}).get("include_agent", True)),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    return result


@router.post("/{recommendation_id}/watch")
def watch(recommendation_id: str, payload: dict, db: Session = Depends(get_db)):
    result = set_watch_status(
        db,
        recommendation_id=recommendation_id,
        watch_status=str(payload.get("watch_status") or "watching"),
        note=payload.get("note"),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    return result


@router.post("/{recommendation_id}/ignore")
def ignore(recommendation_id: str, payload: dict | None = None, db: Session = Depends(get_db)):
    result = set_watch_status(
        db,
        recommendation_id=recommendation_id,
        watch_status="ignored",
        note=(payload or {}).get("note"),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    return result


@router.delete("/{recommendation_id}")
def delete(recommendation_id: str, db: Session = Depends(get_db)):
    ok = delete_recommendation(db, recommendation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="recommendation not found")
    return {"deleted": recommendation_id}


@router.post("/{recommendation_id}/review")
def review(recommendation_id: str, payload: dict, db: Session = Depends(get_db)):
    note = save_review_note(db, recommendation_id, payload)
    if note is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    return note
