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
from app.services.workbench_service import list_skills

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


@router.get("")
def list_strategies(mode: str = Query(default="formal"), db: Session = Depends(get_db)):
    """全部策略清单（含调用覆盖与胜率指标）。

    返回固定顺序：已调用（有样本）的策略在前、按胜率降序；从未调用的策略在后。
    performance=None 表示该项目前无任何推荐历史（未覆盖）。
    """
    if mode not in {"formal", "experiment"}:
        raise HTTPException(status_code=400, detail="mode must be formal or experiment")

    skills = list_skills(db)
    metrics_by_id = {m["strategy_id"]: m for m in metrics_by_strategy(db, mode)}

    result = []
    for s in skills:
        m = metrics_by_id.get(s["skill_id"])
        perf = None
        if m and (m.get("sample_count") or 0) > 0:
            perf = {
                "sample_count": m["sample_count"],
                "labeled_count": m.get("labeled_count", 0),
                "triggered_count": m.get("triggered_count", 0),
                "missed_count": m.get("missed_count", 0),
                "success_count": m.get("success_count", 0),
                "trigger_rate": m.get("trigger_rate"),
                "hit_rate": m.get("hit_rate"),
                "avg_score": m.get("avg_score"),
                "avg_return": m.get("avg_return"),
                "horizon_metrics": m.get("horizon_metrics"),
            }
        result.append(
            {
                "skill_id": s["skill_id"],
                "name": s["name"],
                "description": (s.get("manifest") or {}).get("description") or "",
                "mode": s["mode"],
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
