import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import DailyBar, Recommendation, RecommendationOutcome


def label_pending_outcomes(db: Session, horizon_days: int = 5) -> dict[str, Any]:
    """对 pending 状态的推荐进行结果标注。

    主标注标准（新，更贴近真实可实现收益）:
        close_return >= 3% AND min_return > -4%

    旧标准（保留为 outcome_label_legacy 用于对比）:
        max_return >= 5% AND min_return > -4%

    新增 achievable_return 指标：
        achievable_return = (close_return + min(max_return, close_return * 1.5)) / 2
        含义：在 close_return 与保守估计的 peak 之间取均值，避免用异常 max_return 高估
    """
    recs = db.scalars(
        select(Recommendation).where(Recommendation.status == "pending")
    ).all()
    labeled = 0
    for rec in recs:
        bars = db.scalars(
            select(DailyBar)
            .where(
                DailyBar.code == rec.code,
                DailyBar.trade_date >= rec.trade_date,
            )
            .order_by(DailyBar.trade_date.asc())
            .limit(horizon_days + 1)
        ).all()
        if len(bars) < 2:
            continue

        entry_price = rec.recommend_price or bars[0].close
        # 每日最高收益率
        returns = [
            ((bar.high - entry_price) / entry_price) * 100 for bar in bars[1:]
        ]
        # 期末收盘收益
        close_return = ((bars[-1].close - entry_price) / entry_price) * 100
        # 每日最低收益率（用于计算最大回撤/浮亏）
        low_returns = [
            ((bar.low - entry_price) / entry_price) * 100 for bar in bars[1:]
        ]
        max_return = max(returns) if returns else 0
        min_return = min(low_returns) if low_returns else 0

        # 新可达成收益：在 close_return 与保守 high 之间取中位数
        # min(max_return, close_return * 1.5) 避免 outlier 推高估值
        achievable_return = (
            close_return + min(max_return, close_return * 1.5)
        ) / 2.0

        # -- 新标准命中判断（主标准） --
        hit_new = close_return >= 3 and min_return > -4

        # -- 旧标准（legacy）命中判断 --
        hit_legacy = max_return >= 5 and min_return > -4

        # 主标注使用新标准
        hit_target = hit_new
        hit_invalid = min_return <= -4
        label = "success" if hit_new else "failed"
        label_legacy = "success" if hit_legacy else "failed"

        # outcome_reason 同时说明新旧标准的结果
        new_status = "达标" if hit_new else "未达标"
        legacy_status = "达标" if hit_legacy else "未达标"
        outcome_reason = (
            f"新标准(close_return≥3%&min>-4%): {new_status}"
            f" | 旧标准(max≥5%&min>-4%): {legacy_status}"
        )

        outcome = db.get(RecommendationOutcome, rec.recommendation_id)
        if outcome is None:
            outcome = RecommendationOutcome(recommendation_id=rec.recommendation_id)
            db.add(outcome)
        outcome.horizon_days = horizon_days
        outcome.entry_triggered = True
        outcome.entry_date = bars[1].trade_date
        outcome.entry_price = entry_price
        outcome.max_return = round(max_return, 2)
        outcome.min_return = round(min_return, 2)
        outcome.close_return = round(close_return, 2)
        outcome.achievable_return = round(achievable_return, 2)
        outcome.max_drawdown = round(min_return, 2)
        outcome.hit_target = hit_target
        outcome.hit_invalid = hit_invalid
        outcome.outcome_label = label
        outcome.outcome_label_legacy = label_legacy
        outcome.outcome_reason = outcome_reason
        outcome.labeled_at = datetime.utcnow()
        rec.status = label
        labeled += 1
    db.commit()
    return {"labeled": labeled}


def overview_metrics(db: Session) -> dict[str, Any]:
    """返回推荐结果的全局概览指标。

    使用新标准（close_return >= 3% AND min_return > -4%）计算主命中率，
    旧标准（max_return >= 5% AND min_return > -4%）作为 legacy_hit_rate 对比。
    新增 avg_close_return、avg_achievable_return 等字段。
    """
    outcomes = db.scalars(select(RecommendationOutcome)).all()
    recs = db.scalars(select(Recommendation)).all()
    if not outcomes:
        return {
            "total_recommendations": len(recs),
            "labeled_count": 0,
            "hit_rate": 0,
            "legacy_hit_rate": 0,
            "avg_return": 0,
            "avg_close_return": 0,
            "avg_achievable_return": 0,
            "avg_mfe": 0,
            "avg_mae": 0,
        }

    # 新标准命中率（close_return >= 3% AND min_return > -4%）
    success_new = sum(
        1 for o in outcomes
        if o.close_return is not None and o.close_return >= 3
        and o.min_return is not None and o.min_return > -4
    )
    # 旧标准命中率（max_return >= 5% AND min_return > -4%）
    success_legacy = sum(
        1 for o in outcomes
        if o.max_return is not None and o.max_return >= 5
        and o.min_return is not None and o.min_return > -4
    )
    n = len(outcomes)

    avg_achievable = (
        sum(o.achievable_return for o in outcomes if o.achievable_return is not None) / n
    )

    return {
        "total_recommendations": len(recs),
        "labeled_count": n,
        "hit_rate": round(success_new / n, 3),
        "legacy_hit_rate": round(success_legacy / n, 3),
        "avg_return": round(sum(o.close_return or 0 for o in outcomes) / n, 2),
        "avg_close_return": round(sum(o.close_return or 0 for o in outcomes) / n, 2),
        "avg_achievable_return": round(avg_achievable, 2),
        "avg_mfe": round(sum(o.max_return or 0 for o in outcomes) / n, 2),
        "avg_mae": round(sum(o.min_return or 0 for o in outcomes) / n, 2),
    }


def metrics_by_strategy(db: Session) -> list[dict[str, Any]]:
    """按策略维度汇总成功率、平均分等指标。

    使用新标准（close_return >= 3% AND min_return > -4%）判定成功率，
    从 outcome.outcome_label 读取主标注结果。
    """
    recs = db.scalars(select(Recommendation)).all()
    buckets: dict[str, dict[str, Any]] = {}
    outcomes = {
        o.recommendation_id: o
        for o in db.scalars(select(RecommendationOutcome)).all()
    }
    for rec in recs:
        signals = json.loads(rec.skill_breakdown_json or "[]")
        for signal in signals:
            sid = signal.get("strategy_id", "unknown")
            item = buckets.setdefault(
                sid,
                {
                    "strategy_id": sid,
                    "sample_count": 0,
                    "success_count": 0,
                    "avg_score": 0.0,
                },
            )
            item["sample_count"] += 1
            item["avg_score"] += float(signal.get("score", 0))
            outcome = outcomes.get(rec.recommendation_id)
            if outcome is not None and outcome.outcome_label == "success":
                item["success_count"] += 1
    result = []
    for item in buckets.values():
        count = max(1, item["sample_count"])
        item["hit_rate"] = round(item["success_count"] / count, 3)
        item["avg_score"] = round(item["avg_score"] / count, 2)
        result.append(item)
    return sorted(result, key=lambda x: x["sample_count"], reverse=True)
