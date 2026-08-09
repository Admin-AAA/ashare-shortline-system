"""策略清单 API —— 全部策略元数据 + 当前版本半年 canonical 回测画像。

填补前端"策略清单页"缺口：用户在一个页面看到系统全部策略（中文名 + 简介 + 启用状态 +
调用覆盖 + 胜率），一眼看清哪些策略在 work、哪些尚未完成当前版本回测。

数据来源：
- 策略元信息：workbench_service.list_skills（SkillRegistry + manifest，含中文名/简介/预设/回测能力）
- 表现画像：backtest_runs_v2/backtest_trades 中精确匹配当前版本、参数、成交政策和半年窗口的结果
绝不回退到跨版本混合的真实推荐收益。
"""

import logging
import sys
import time
from pathlib import Path

from fastapi import Depends, HTTPException, Query
from app.api.contracts import ContractRouter as APIRouter
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.backtest.strategy_profile import StrategyBacktestProfiles
from app.services.outcome_service import exposure_sample_counts
from app.services.strategy_center_service import (
    StrategyVersionNotFoundError,
    get_strategy_center_detail,
)
from app.services.workbench_service import list_skills

logger = logging.getLogger(__name__)

# 进程级 TTL 缓存：gate_blocked_detail 查 14 天 experiment run LIKE，
# 60s 内不重复查（补跑 run 每 8:03 才产生新数据，60s 足够）
_GATE_CACHE: dict[str, tuple[float, dict | None]] = {}
_GATE_CACHE_TTL = 60.0  # seconds

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
    profile: dict | None,
    *,
    exposure_30d: int = 0,
    last_exp_gate: dict | None = None,
) -> dict | None:
    if not profile:
        return None
    sample_count = int(profile.get("executed_trade_count") or 0)
    all_trades = int(profile.get("sample_count") or 0)
    payload = {
        "source": "canonical_backtest",
        "strategy_version": profile.get("strategy_version"),
        "profile_fingerprint": profile.get("profile_fingerprint"),
        "backtest_id": profile.get("backtest_id"),
        "portfolio_model": profile.get("portfolio_model"),
        "sample_count": sample_count,
        "labeled_count": sample_count,
        "triggered_count": sample_count,
        "missed_count": max(0, all_trades - sample_count),
        "success_count": int(profile.get("win_count") or 0),
        "trigger_rate": sample_count / all_trades if all_trades else 0.0,
        "hit_rate": float(profile.get("win_rate_pct") or 0) / 100,
        "avg_score": None,
        "avg_return": profile.get("average_net_return_pct"),
        "profit_loss_ratio": profile.get("profit_loss_ratio"),
        "total_return_pct": profile.get("total_return_pct"),
        "max_drawdown_pct": profile.get("max_drawdown_pct"),
        "window_metrics": profile.get("window_metrics") or {},
        "quality": profile.get("quality"),
        "monthly_returns": profile.get("monthly_returns") or [],
        "date_from": profile.get("date_from"),
        "date_to": profile.get("date_to"),
        "target_date_from": profile.get("target_date_from"),
        "target_date_to": profile.get("target_date_to"),
        "window_rolled": bool(profile.get("window_rolled")),
        "generated_at": profile.get("generated_at"),
        "reliable": sample_count >= MIN_SAMPLE_FOR_RELIABLE,
        "exposure_30d": int(exposure_30d or 0),
        "exposure_floor": EXPOSURE_SAMPLE_FLOOR,
    }
    if last_exp_gate is not None:
        payload["last_exp_gate"] = last_exp_gate
    return payload


def _wrap_gate_detail(det: dict) -> dict:
    """把 _build_gate_detail 的 raw 输出包装为 API 响应格式（加 gate_blocked）。

    与 _last_exp_gate_for 逻辑一致，但直接从已查出的 det 构造，
    不再走 DB 查询（批量已查出 raw，避免 N+1）。
    """
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
        "panic_day": bool(det.get("panic_day")),
        "limit_down_count": det.get("limit_down_count"),
        "limit_up_count": det.get("limit_up_count"),
        "regime": det.get("regime"),
        "trade_date": det.get("trade_date"),
    }


def _last_exp_gate_for(db: Session, skill_id: str) -> dict | None:
    """最近 completed experiment 补跑门线明细（有效线 vs 最高候选）。

    仅供曝光 < floor 策略展示「门线拦截」；无数据返回 None。
    附 panic_day/ld/regime（self_check T28）——有效线 89–101 在恐慌日属设计内。
    60s TTL 缓存：补跑 run 每 8:03 才产生新数据，连续请求不重复 LIKE 查询。
    """
    now = time.monotonic()
    cached = _GATE_CACHE.get(skill_id)
    if cached and (now - cached[0]) < _GATE_CACHE_TTL:
        return cached[1]
    det = self_check._fetch_gate_blocked_detail(db, skill_id)
    if not det:
        result = None
    else:
        result = {
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
            "panic_day": bool(det.get("panic_day")),
            "limit_down_count": det.get("limit_down_count"),
            "limit_up_count": det.get("limit_up_count"),
            "regime": det.get("regime"),
            "trade_date": det.get("trade_date"),
        }
    _GATE_CACHE[skill_id] = (now, result)
    return result


@router.get("")
def list_strategies(
    db: Session = Depends(get_db),
):
    """全部策略清单（当前版本半年 canonical 回测，缺失时明确 pending）。

    返回固定顺序：已调用（有样本）的策略在前、按胜率降序；从未调用的策略在后。
    performance=None 表示当前版本、参数与窗口尚无正式画像；不会读取历史推荐补位。
    """
    skills = list_skills(db)
    profiles = StrategyBacktestProfiles(db)
    try:
        targets = profiles.current_targets()
    except ValueError:
        targets = ()
    target_by_id = {target.strategy_id: target for target in targets}
    # 窗口随交易日漂移（每来一个新交易日整体平移一天）。read_many_rolled 优先精确匹配
    # 当前目标窗口，miss 时回退到同一 strategy_version 下最新完成的画像并标注 window_rolled，
    # 不跨版本混合——避免窗口每天漂移导致页面长期「尚未生成」。
    profiles_by_id = profiles.read_many_rolled(targets)
    exposure_30d = exposure_sample_counts(db, window_days=30)

    # T4：低曝光策略门线明细**批量**查询（1 次索引扫描替代 N 次全扫）
    low_sids = [
        s["skill_id"] for s in skills
        if int(exposure_30d.get(s["skill_id"]) or 0) < EXPOSURE_SAMPLE_FLOOR
    ]
    raw_gate_map = self_check.fetch_gate_blocked_details(db, low_sids)
    # _last_exp_gate_for 包装 raw detail 加 gate_blocked/panic_day 等字段（与 strategy_detail 端点一致）
    gate_map: dict[str, dict | None] = {}
    for sid in low_sids:
        raw = raw_gate_map.get(sid)
        if raw is None:
            gate_map[sid] = None
            continue
        # 缓存 raw detail 供 _last_exp_gate_for 的 TTL 缓存使用（避免重复 LIKE 查询）
        now = time.monotonic()
        _GATE_CACHE[sid] = (now, raw)
        gate_map[sid] = _wrap_gate_detail(raw)
    result = []
    for s in skills:
        sid = s["skill_id"]
        target = target_by_id.get(sid)
        profile = profiles_by_id.get(sid)
        exp_n = int(exposure_30d.get(sid) or 0)
        # 仅低曝光策略展示门线明细（gate_map 已批量查出，非 N 次全扫）
        gate = gate_map.get(sid) if exp_n < EXPOSURE_SAMPLE_FLOOR else None
        perf = _performance_payload(profile, exposure_30d=exp_n, last_exp_gate=gate)
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
            "performance_status": (
                "ready"
                if perf is not None
                else ("pending_backtest" if target is not None else "unavailable")
            ),
            "current_strategy_version": (
                target.strategy_version if target is not None else None
            ),
            "performance": perf,
        }
        if gate is not None:
            item["last_exp_gate"] = gate
        result.append(item)

    result.sort(
        key=lambda x: (
            x["performance_status"] == "ready",
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
    profiles = StrategyBacktestProfiles(db)
    target = profiles.current_target(strategy_id)
    profile = (
        profiles.read_rolled(target)
        if target is not None and detail.get("selected_is_current")
        else None
    )
    exp_n = int(exposure_sample_counts(db, window_days=30).get(strategy_id) or 0)
    gate = _last_exp_gate_for(db, strategy_id) if exp_n < EXPOSURE_SAMPLE_FLOOR else None
    detail["performance"] = _performance_payload(profile, exposure_30d=exp_n, last_exp_gate=gate)
    detail["performance_status"] = (
        "ready"
        if profile is not None
        else (
            "pending_backtest"
            if target is not None and detail.get("selected_is_current")
            else "selected_version_not_current"
        )
    )
    detail["exposure_30d"] = exp_n
    detail["exposure_floor"] = EXPOSURE_SAMPLE_FLOOR
    if gate is not None:
        detail["last_exp_gate"] = gate
    return detail
