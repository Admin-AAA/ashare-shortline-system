#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trailing stop 参数 · 零拷贝反事实扫参
=====================================================================

动机（复盘数据 2026-07）：以 stop_loss 出场的 68 笔 formal 已入场推荐中，85%
持有期内最高价曾冲过 +2% 的 trailing 激活线（平均冲到 +5.56%），最终却平均
-6.63% 止损出场。表面看"本可被 trailing 保护、错过 +12% 浮盈"。

但 `max_return` 是收盘口径最高价，trailing 按 high 激活、按 low 触发、且
stop_loss 优先（保守口径）。同 bar 冲高后回落打止损，日线无法判断 high/low
先后——这很可能是**日线时序限制**，而非参数问题。本工具用与生产完全一致的
`_simulate_outcome`（入场时序/滑点/除权全复用）做 activate×callback 网格扫参，
实证当前 2.0/0.8 是否最优，而不是拍脑袋调参。

护栏（沿用 outcome_simulation.py 第 162 轮结论）：
- 只允许 stop_loss 优先的保守口径；**不测试 trailing 优先变体**（日线时序不确定
  下会产生虚假 +11pp 胜率，纯过拟合）。
- 评判看端到端指标（胜率/盈亏比/最大亏损/Sharpe），不以"少亏"单一维度论英雄。
  更紧的 callback 会同时把部分 take_profit/trailing 盈利单提前震出，可能净亏。

只读，不写 DB，不改生产常量。
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backtest_cost_aware import BacktestRow, compute_metrics, dedup_rows  # noqa: E402


def make_trailing_resolver(activate_pct: float, callback_pct: float):
    """生成一个参数化的 trailing 退出解析器（签名同生产 _resolve_exit）。

    完全复制生产 `_resolve_exit_with_trailing` 的两轮结构，仅把
    TRAILING_STOP_ACTIVATE_PCT / TRAILING_STOP_CALLBACK_PCT 换成参数。
    保持 stop_loss 优先（保守口径），不做 trailing 优先变体。
    """
    from app.services.outcome_simulation import (
        STOP_LOSS_GAP_CAP_PCT,
        _as_float,
        _exit_execution_block_reason,
        _resolve_exit_base,
    )

    def resolver(holding_bars, stop_loss_price, take_profit_price, *, code="", entry_price=0.0):
        # 与生产一致：T+1，入场日不离场
        if len(holding_bars) <= 1:
            last = holding_bars[-1]
            return _as_float(last.close or 0), str(last.trade_date), "horizon_close_t1_locked"
        scan_bars = holding_bars[1:]

        base_exit = _resolve_exit_base(
            scan_bars, stop_loss_price, take_profit_price, code=code, entry_price=entry_price
        )
        if base_exit[2].startswith("take_profit"):
            return base_exit

        # 第二轮：trailing（stop_loss 优先保守口径）
        locked = 0
        hwm = 0.0
        activated = False
        for bar in scan_bars:
            if _exit_execution_block_reason(code, bar):
                locked += 1
                continue
            bar_open = _as_float(bar.open)
            high = _as_float(bar.high)
            low = _as_float(bar.low)
            if bar_open > 0 and low > 0 and bar_open <= stop_loss_price:
                if entry_price > 0:
                    min_exit = entry_price * (1 + STOP_LOSS_GAP_CAP_PCT / 100.0)
                    gap_exit = max(bar_open, min_exit)
                else:
                    gap_exit = bar_open
                return gap_exit, str(bar.trade_date), "stop_loss_gap"
            activated_this_bar = False
            if entry_price > 0 and high > 0:
                bar_high_ret = (high / entry_price - 1) * 100
                if bar_high_ret > hwm:
                    hwm = bar_high_ret
                if not activated and hwm >= activate_pct:
                    activated = True
                    activated_this_bar = True
            # stop_loss 优先（保守口径，禁止改 trailing 优先）
            if low > 0 and low <= stop_loss_price:
                return stop_loss_price, str(bar.trade_date), "stop_loss_hit"
            if activated and entry_price > 0 and low > 0:
                tsl_ret = hwm - callback_pct
                tsl_price = entry_price * (1 + tsl_ret / 100.0)
                if low <= tsl_price:
                    exit_px = tsl_price if activated_this_bar else (
                        min(bar_open, tsl_price) if bar_open > 0 else tsl_price
                    )
                    return exit_px, str(bar.trade_date), "trailing_stop_hit"
        last_bar = scan_bars[-1]
        if locked and locked == len(scan_bars):
            return _as_float(last_bar.close), str(last_bar.trade_date), "limit_down_locked_horizon_mark"
        if locked:
            return _as_float(last_bar.close), str(last_bar.trade_date), "horizon_close_after_locked_limit_down"
        return _as_float(last_bar.close), str(last_bar.trade_date), "horizon_close"

    return resolver


def _strategy_ids(skill_breakdown_json) -> List[str]:
    if not skill_breakdown_json:
        return []
    try:
        sb = json.loads(skill_breakdown_json)
    except Exception:
        return []
    if isinstance(sb, list):
        return [it.get("strategy_id") for it in sb if isinstance(it, dict) and it.get("strategy_id")]
    return []


def evaluate_trailing_grid(
    db,
    activates: Tuple[float, ...] = (1.5, 2.0, 2.5, 3.0),
    callbacks: Tuple[float, ...] = (0.5, 0.8, 1.2, 1.5),
    run_mode: Optional[str] = "formal",
) -> dict:
    """对历史已结算推荐，用生产 _simulate_outcome + 注入式 trailing 解析器扫参。

    返回 {(activate, callback): metrics}。只读，不写 DB。
    """
    from sqlalchemy import select

    from app.models.tables import Recommendation, RecommendationOutcome, RecommendationRun
    from app.services.outcome_service import _load_outcome_bars, _simulate_outcome

    stmt = (
        select(Recommendation, RecommendationRun.run_mode)
        .join(
            RecommendationOutcome,
            RecommendationOutcome.recommendation_id == Recommendation.recommendation_id,
        )
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .where(RecommendationOutcome.outcome_label.in_(["success", "failed"]))
        .where(RecommendationOutcome.entry_triggered == 1)  # type: ignore[attr-defined]
    )
    rows_all = db.execute(stmt).all()
    if run_mode:
        rows_all = [t for t in rows_all if t[1] == run_mode]
    recs = [t[0] for t in rows_all]

    bars_by_code, dates_by_code = _load_outcome_bars(db, recs)

    # 先确定生产 horizon（沿用默认 5，与 exit_rules.py 一致）
    horizon_days = 5

    results: Dict[Tuple[float, float], dict] = {}
    for act in activates:
        for cb in callbacks:
            resolver = make_trailing_resolver(act, cb)
            rows: List[BacktestRow] = []
            for rec in recs:
                code = str(rec.code)
                sim = _simulate_outcome(
                    rec,
                    bars_by_code.get(code, []),
                    dates_by_code.get(code, []),
                    horizon_days=horizon_days,
                    exit_resolver=resolver,
                )
                if sim is None or not sim.get("entry_triggered", True):
                    continue
                fs = json.loads(rec.feature_snapshot_json) if rec.feature_snapshot_json else {}
                rows.append(BacktestRow(
                    recommendation_id=rec.recommendation_id,
                    trade_date=str(rec.trade_date),
                    code=code,
                    executed_return=float(sim["executed_return"]),
                    outcome_label=str(sim["outcome_label"]),
                    exit_reason=str(sim.get("exit_reason", "")),
                    holding_days=int(sim.get("holding_days") or 0),
                    final_score=float(rec.final_score or 0.0),
                    strategy_ids=_strategy_ids(rec.skill_breakdown_json),
                    phase=fs.get("market_sentiment_phase"),
                ))
            results[(act, cb)] = compute_metrics(dedup_rows(rows))
    return {"grid": results, "recs_total": len(recs), "horizon_days": horizon_days}


def render_grid_report(result: dict) -> str:
    grid = result["grid"]
    acts = sorted({k[0] for k in grid})
    cbs = sorted({k[1] for k in grid})
    lines = []
    lines.append("# trailing stop 参数 · 零拷贝反事实扫参报告")
    lines.append("")
    lines.append(f"- 重模拟推荐数(formal 已入场): {result.get('recs_total')}  horizon={result.get('horizon_days')}天")
    lines.append(f"- activate 候选: {acts}  callback 候选: {cbs}")
    lines.append("- 口径: stop_loss 优先（保守）；不测试 trailing 优先变体")
    lines.append("")
    lines.append("## 平均净收益 avg_net_return (%)  [行=activate, 列=callback]")
    lines.append("")
    header = "| activate\\callback |" + "|".join(f" {cb} " for cb in cbs) + "|"
    lines.append(header)
    lines.append("|" + "---|" * (len(cbs) + 1))
    for act in acts:
        row = f"| {act} |"
        for cb in cbs:
            row += f" {grid[(act,cb)]['avg_net_return']:+.2f} |"
        lines.append(row)
    lines.append("")
    lines.append("## 胜率 win_rate (%)")
    lines.append("")
    lines.append(header)
    lines.append("|" + "---|" * (len(cbs) + 1))
    for act in acts:
        row = f"| {act} |"
        for cb in cbs:
            row += f" {grid[(act,cb)]['win_rate']*100:.1f} |"
        lines.append(row)
    lines.append("")
    lines.append("## 盈亏比 pl_ratio")
    lines.append("")
    lines.append(header)
    lines.append("|" + "---|" * (len(cbs) + 1))
    for act in acts:
        row = f"| {act} |"
        for cb in cbs:
            row += f" {grid[(act,cb)]['pl_ratio']:.2f} |"
        lines.append(row)
    lines.append("")
    lines.append("## 最大亏损 max_loss_net (%)（越接近0越好）")
    lines.append("")
    lines.append(header)
    lines.append("|" + "---|" * (len(cbs) + 1))
    for act in acts:
        row = f"| {act} |"
        for cb in cbs:
            row += f" {grid[(act,cb)]['max_loss_net']:+.2f} |"
        lines.append(row)
    lines.append("")

    # 与生产(2.0, 0.8)对比
    base = grid.get((2.0, 0.8))
    if base:
        lines.append("## 相对生产 (2.0, 0.8) 的净收益变化 (pp)")
        lines.append("")
        lines.append(header)
        lines.append("|" + "---|" * (len(cbs) + 1))
        for act in acts:
            row = f"| {act} |"
            for cb in cbs:
                d = grid[(act, cb)]["avg_net_return"] - base["avg_net_return"]
                row += f" {d:+.2f} |"
            lines.append(row)
        lines.append("")
    return "\n".join(lines)


def main() -> dict:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        result = evaluate_trailing_grid(db)
    finally:
        db.close()
    print(render_grid_report(result))
    return result


if __name__ == "__main__":
    main()
