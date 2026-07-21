"""策略清单 API —— 全部策略的元数据 + 实盘/回测胜率，单次返回。

填补前端"策略清单页"缺口：用户在一个页面看到系统全部策略（中文名 + 简介 + 启用状态 +
调用覆盖 + 胜率），一眼看清哪些策略在 work、哪些从未被调用（覆盖 3/8 → 8/8 的入口）。

数据来源：
- 策略元信息：workbench_service.list_skills（SkillRegistry + manifest，含中文名/简介/预设/回测能力）
- 胜率指标：outcome_service.metrics_by_strategy（按 skill_breakdown_json 聚合的胜率/样本/收益）
两者在 strategy_id 上 join；从未被调用的策略 performance=None（明确标记为"未覆盖"）。
"""

import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.outcome_service import exposure_sample_counts, metrics_by_strategy
from app.services.strategy_center_service import (
    StrategyVersionNotFoundError,
    get_strategy_center_detail,
)
from app.services.workbench_service import list_skills

# 统计可信阈值：与 scheduler_service.DEFAULT_EXPOSURE_MIN_SAMPLE=20 对齐。
MIN_SAMPLE_FOR_RELIABLE = 20
# E 项曝光样本下限（与 self_check SAMPLE_FLOOR / DEFAULT_EXPOSURE_MIN_SAMPLE 一致）
EXPOSURE_SAMPLE_FLOOR = 20

# self_check 门线明细（scripts/）——与 E 项 gate_blocked 同源，避免二次实现漂移
_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import self_check  # type: ignore[import-not-found]  # noqa: E402

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


def _performance_payload(
    metric: dict | None,
    *,
    exposure_30d: int = 0,
    last_exp_gate: dict | None = None,
) -> dict | None:
    if not metric or (metric.get("sample_count") or 0) <= 0:
        return None
    payload = {
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
        "exposure_30d": int(exposure_30d or 0),
        "exposure_floor": EXPOSURE_SAMPLE_FLOOR,
        "horizon_metrics": metric.get("horizon_metrics"),
        "date_range": metric.get("date_range"),
    }
    if last_exp_gate is not None:
        payload["last_exp_gate"] = last_exp_gate
    return payload


def _last_exp_gate_for(db: Session, skill_id: str) -> dict | None:
    """最近 completed experiment 补跑门线明细（有效线 vs 最高候选）。

    仅供曝光 < floor 策略展示「门线拦截」；无数据返回 None。
    """
    det = self_check._fetch_gate_blocked_detail(db, skill_id)
    if not det:
        return None
    return {
        "effective_line": det.get("effective_line"),
        "max_candidate_score": det.get("max_candidate_score"),
        "base_score_line": det.get("base_score_line"),
        "gate_blocked": (
            det.get("max_candidate_score") is None
            or (
                det.get("effective_line") is not None
                and det.get("max_candidate_score") is not None
                and float(det["max_candidate_score"]) < float(det["effective_line"])
            )
        ),
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
    exposure_30d = exposure_sample_counts(db, window_days=30)

    result = []
    for s in skills:
        sid = s["skill_id"]
        m = metrics_by_id.get(sid)
        exp_n = int(exposure_30d.get(sid) or 0)
        # 仅低曝光策略查门线明细（N 小，避免全量 skill 扫 filter_audit）
        gate = _last_exp_gate_for(db, sid) if exp_n < EXPOSURE_SAMPLE_FLOOR else None
        perf = _performance_payload(m, exposure_30d=exp_n, last_exp_gate=gate)
        item = {
            "skill_id": sid,
            "name": s["name"],
            "description": (s.get("manifest") or {}).get("description") or "",
            "enabled": s["enabled"],
            "source_type": s.get("source_type"),
            "assigned_presets": s.get("assigned_presets") or [],
            "has_backtest_adapter": s.get("has_backtest_adapter", False),
            "exposure_30d": exp_n,
            "exposure_floor": EXPOSURE_SAMPLE_FLOOR,
            "performance": perf,
        }
        if gate is not None:
            item["last_exp_gate"] = gate
        result.append(item)

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
    exp_n = int(exposure_sample_counts(db, window_days=30).get(strategy_id) or 0)
    gate = _last_exp_gate_for(db, strategy_id) if exp_n < EXPOSURE_SAMPLE_FLOOR else None
    detail["performance"] = _performance_payload(metric, exposure_30d=exp_n, last_exp_gate=gate)
    detail["exposure_30d"] = exp_n
    detail["exposure_floor"] = EXPOSURE_SAMPLE_FLOOR
    if gate is not None:
        detail["last_exp_gate"] = gate
    return detail
