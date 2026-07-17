"""策略清单 API —— 全部策略的元数据 + 实盘/回测胜率，单次返回。

填补前端"策略清单页"缺口：用户在一个页面看到系统全部策略（中文名 + 简介 + 启用状态 +
调用覆盖 + 胜率），一眼看清哪些策略在 work、哪些从未被调用（覆盖 3/8 → 8/8 的入口）。

数据来源：
- 策略元信息：workbench_service.list_skills（SkillRegistry + manifest，含中文名/简介/预设/回测能力）
- 胜率指标：outcome_service.metrics_by_strategy（按 skill_breakdown_json 聚合的胜率/样本/收益）
两者在 strategy_id 上 join；从未被调用的策略 performance=None（明确标记为"未覆盖"）。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.outcome_service import metrics_by_strategy
from app.services.strategy_center_service import (
    StrategyVersionNotFoundError,
    get_strategy_center_detail,
)
from app.services.workbench_service import list_skills

# 统计可信阈值：与 scheduler_service.DEFAULT_EXPOSURE_MIN_SAMPLE=20 对齐。
# 低于此样本数的策略，其胜率/盈亏比属统计噪声，前端须标注「样本不足 · 统计参考」，
# 防止用户对 n<20 的策略过度信任（self_check 长期预警：5 策略样本<20 胜率不可信）。
MIN_SAMPLE_FOR_RELIABLE = 20

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


def _performance_payload(metric: dict | None) -> dict | None:
    if not metric or (metric.get("sample_count") or 0) <= 0:
        return None
    return {
        "sample_count": metric["sample_count"],
        "labeled_count": metric.get("labeled_count", 0),
        "triggered_count": metric.get("triggered_count", 0),
        "missed_count": metric.get("missed_count", 0),
        "success_count": metric.get("success_count", 0),
        "trigger_rate": metric.get("trigger_rate"),
        "hit_rate": metric.get("hit_rate"),
        "avg_score": metric.get("avg_score"),
        "avg_return": metric.get("avg_return"),
        "profit_loss_ratio": metric.get("profit_loss_ratio"),
        "last_recommendation_date": metric.get("last_recommendation_date"),
        "reliable": (metric.get("sample_count") or 0) >= MIN_SAMPLE_FOR_RELIABLE,
        "horizon_metrics": metric.get("horizon_metrics"),
        "date_range": metric.get("date_range"),
    }


@router.get("")
def list_strategies(
    db: Session = Depends(get_db),
    window_days: int | None = Query(default=None, ge=1, le=365, description="滚动窗口天数，如 30/90；设置后自动截到已结算截止日"),
    start_date: str | None = Query(default=None, description="显式起始日期 YYYY-MM-DD（window_days 优先）"),
    end_date: str | None = Query(default=None, description="显式截止日期 YYYY-MM-DD（window_days 优先）"),
):
    """全部策略清单（含调用覆盖与胜率指标）。

    返回固定顺序：已调用（有样本）的策略在前、按胜率降序；从未调用的策略在后。
    performance=None 表示该项目前无任何推荐历史（未覆盖）。
    时间窗口：window_days/start_date/end_date 透传给 metrics_by_strategy，
    与 /api/metrics/by-strategy 口径一致，便于策略清单页与指标页跨页对照。
    """
    skills = list_skills(db)
    metrics_by_id = {
        m["strategy_id"]: m
        for m in metrics_by_strategy(db, window_days=window_days, start_date=start_date, end_date=end_date)
    }

    result = []
    for s in skills:
        m = metrics_by_id.get(s["skill_id"])
        perf = _performance_payload(m)
        result.append(
            {
                "skill_id": s["skill_id"],
                "name": s["name"],
                "description": (s.get("manifest") or {}).get("description") or "",
                "enabled": s["enabled"],
                "source_type": s.get("source_type"),
                "assigned_presets": s.get("assigned_presets") or [],
                "has_backtest_adapter": s.get("has_backtest_adapter", False),
                "performance": perf,
            }
        )

    # 已调用的排前面（按胜率降序），从未调用的排后面
    result.sort(
        key=lambda x: (
            x["performance"] is not None,
            x["performance"]["hit_rate"]
            if x["performance"] and x["performance"]["hit_rate"] is not None
            else -1,
        ),
        reverse=True,
    )
    return result


@router.get("/{strategy_id}")
def strategy_detail(
    strategy_id: str,
    version_id: str | None = Query(default=None, description="查看指定的不可变策略版本快照"),
    db: Session = Depends(get_db),
):
    try:
        detail = get_strategy_center_detail(db, strategy_id, version_id=version_id)
    except StrategyVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="strategy version not found") from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    metric = next(
        (item for item in metrics_by_strategy(db) if item.get("strategy_id") == strategy_id),
        None,
    )
    detail["performance"] = _performance_payload(metric)
    return detail
