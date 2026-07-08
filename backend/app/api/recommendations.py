from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case
from sqlalchemy.orm import Session  # noqa: F401 (Query used in endpoints)

from app.db.session import get_db
from app.services.outcome_service import recent_formal_win_rate
from app.services.workbench_service import (
    delete_recommendation,
    get_recommendation_detail,
    get_run_replay_analysis,
    list_recommendation_history,
    save_review_note,
    set_watch_status,
)

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


@router.get("/history")
def history(
    code: str | None = Query(default=None),
    label: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    bucket: str | None = Query(default=None),
    setup_type: str | None = Query(default=None),
    strategy_id: str | None = Query(default=None, description="按策略下钻：仅返回 skill_breakdown 含该 strategy_id 的推荐"),
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
    return list_recommendation_history(
        db,
        code=code,
        label=label,
        date_from=date_from,
        date_to=date_to,
        bucket=bucket,
        setup_type=setup_type,
        strategy_id=strategy_id,
        limit=limit,
        offset=offset,
    )


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
    from app.models.tables import Recommendation, RecommendationOutcome
    from sqlalchemy import func

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
            func.count(RecommendationOutcome.recommendation_id).label("settled"),
            func.sum(case((RecommendationOutcome.outcome_label == "success", 1), else_=0)).label("wins"),
            func.avg(RecommendationOutcome.executed_return).label("avg_return"),
        )
        .join(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
        .filter(Recommendation.code.in_(normalized))
        .filter(Recommendation.recommendation_id.like("rec_%"))
        .filter(RecommendationOutcome.outcome_label.in_(["success", "failed"]))
        .group_by(Recommendation.code)
        .all()
    )

    result: dict[str, dict] = {}
    for code, settled, wins, avg_return in rows:
        settled = int(settled or 0)
        wins = int(wins or 0)
        result[code] = {
            "settled": settled,
            "wins": wins,
            "win_rate": round(wins * 100.0 / settled, 1) if settled else 0.0,
            "avg_return": round(float(avg_return or 0.0), 2),
        }
    # 补齐「请求了但无历史」的代码，前端据此区分「无历史」与「未查询」
    for code in normalized:
        result.setdefault(code, {"settled": 0, "wins": 0, "win_rate": 0.0, "avg_return": 0.0})
    return result


@router.get("/win-rate")
def win_rate(
    window_days: int = Query(default=5, ge=1, le=60, description="近 N 个已完成 formal 交易日"),
    db: Session = Depends(get_db),
):
    """近 N 个交易日 formal 推荐聚合胜率/均收益（驾驶舱状态条「近 N 日胜率」真实数据源）。

    只统计实盘 rec_ 推荐中已结算的 outcome，未结算不计入分母（诚实，不虚构胜率）；
    按交易日而非日历日取最近 N 个已完成 formal 交易日，避免空窗虚算。
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
