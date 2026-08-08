#!/usr/bin/env python3
"""按用户实际费率研究三条候选短线方向；只读数据库。"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import and_, select

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import BacktestCandidate, BacktestInputArtifact, BacktestRunV2, BacktestTrade  # noqa: E402
from app.services.backtest.strategy_profile import (  # noqa: E402
    STRATEGY_PROFILE_EXPERIMENT_ID,
    StrategyBacktestProfiles,
)

from run_strategy_attribution_matrix import first_stock, regime_name  # noqa: E402


COMMISSION_RATE_PER_SIDE = 0.000085  # 万分之0.85
STAMP_TAX_RATE_ON_EXIT = 0.0005  # 卖出印花税万分之5
CAPITAL_SCENARIOS = (20_000.0, 50_000.0, 100_000.0)

TARGETS = {
    "limit-up-reseal-relay": {
        "name": "涨停回封",
        "anchor": "回封确认",
        "regime": "震荡",
        "score": 85.0,
    },
    "small-cap-thematic-momentum": {
        "name": "小盘题材主线",
        "anchor": "小盘主线共振",
        "regime": "强势",
        "score": 85.0,
    },
    "trend-pullback-continuation": {
        "name": "趋势缩量回踩",
        "anchor": "缩量回踩确认",
        "regime": "强势",
        "score": 85.0,
    },
}


def actual_fee_pct(capital: float, position_size: float) -> float:
    """Return round-trip fee in percentage points of the traded notional."""
    notional = capital * max(position_size, 0.0)
    if notional <= 0:
        return 0.0
    commission = max(notional * COMMISSION_RATE_PER_SIDE, 1.0)
    total_fee = commission * 2 + notional * STAMP_TAX_RATE_ON_EXIT
    return total_fee / notional * 100.0


def load_observations(db: Any) -> dict[str, list[dict[str, Any]]]:
    service = StrategyBacktestProfiles(db)
    targets = {
        target.strategy_id: target
        for target in service.current_targets()
        if target.strategy_id in TARGETS
    }
    runs: dict[str, BacktestRunV2] = {}
    for strategy_id, target in targets.items():
        run = db.scalar(
            select(BacktestRunV2)
            .where(
                BacktestRunV2.experiment_id == STRATEGY_PROFILE_EXPERIMENT_ID,
                BacktestRunV2.strategy_id == strategy_id,
                BacktestRunV2.strategy_version == target.strategy_version,
                BacktestRunV2.date_from == target.date_from,
                BacktestRunV2.date_to == target.date_to,
                BacktestRunV2.status == "completed",
            )
            .order_by(BacktestRunV2.finished_at.desc(), BacktestRunV2.created_at.desc())
        )
        if run is not None:
            runs[strategy_id] = run

    dates_by_snapshot: dict[str, set[str]] = defaultdict(set)
    for run in runs.values():
        dates = db.scalars(
            select(BacktestTrade.signal_date)
            .where(BacktestTrade.backtest_id == run.backtest_id)
            .distinct()
        ).all()
        dates_by_snapshot[run.snapshot_id].update(str(value) for value in dates)

    regimes: dict[str, dict[str, str]] = {}
    for snapshot_id, dates in dates_by_snapshot.items():
        by_date: dict[str, str] = {}
        rows = db.execute(
            select(BacktestInputArtifact.signal_date, BacktestInputArtifact.payload).where(
                BacktestInputArtifact.snapshot_id == snapshot_id,
                BacktestInputArtifact.artifact_scope == "signal_input",
                BacktestInputArtifact.signal_date.in_(sorted(dates)),
            )
        ).yield_per(20)
        for signal_date, payload in rows:
            stock = first_stock(bytes(payload))
            if stock is not None:
                by_date[str(signal_date)] = regime_name(stock.get("market_regime"))
        regimes[snapshot_id] = by_date

    observations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for strategy_id, run in runs.items():
        stmt = (
            select(BacktestTrade.trade_json, BacktestCandidate.signal_json)
            .outerjoin(
                BacktestCandidate,
                and_(
                    BacktestCandidate.backtest_id == BacktestTrade.backtest_id,
                    BacktestCandidate.signal_date == BacktestTrade.signal_date,
                    BacktestCandidate.code == BacktestTrade.code,
                ),
            )
            .where(BacktestTrade.backtest_id == run.backtest_id)
        )
        for trade_json, signal_json in db.execute(stmt).yield_per(1000):
            trade = json.loads(str(trade_json))
            if trade.get("entry_date") is None or trade.get("outcome_label") == "not_executed":
                continue
            signal = json.loads(str(signal_json)) if signal_json else {}
            phase = str(signal.get("phase") or "未标注")
            setup = str(signal.get("setup_match") or signal.get("entry_rule") or "未标注")
            if phase in {"未分类", "未标注", ""} and setup not in {"未分类", "未标注", ""}:
                phase = setup
            score = float(signal["score"]) if signal.get("score") is not None else None
            observations[strategy_id].append(
                {
                    "signal_date": str(trade.get("signal_date")),
                    "regime": regimes.get(run.snapshot_id, {}).get(str(trade.get("signal_date")), "未知"),
                    "phase": phase,
                    "score": score,
                    "position_size": float(trade.get("position_size") or 0.0),
                    "gross": float(trade.get("gross_return_pct") or 0.0),
                    "exit": str(trade.get("exit_reason") or "未标注"),
                }
            )
    return observations


def selected(
    rows: list[dict[str, Any]],
    *,
    anchor_value: str | None = None,
    regime: str | None = None,
    high_score: bool = False,
) -> list[dict[str, Any]]:
    result = rows
    if anchor_value is not None:
        result = [row for row in result if row["phase"] == anchor_value]
    if regime is not None:
        result = [row for row in result if row["regime"] == regime]
    if high_score:
        result = [row for row in result if row["score"] is not None and row["score"] >= 85.0]
    return result


def avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def metrics(rows: list[dict[str, Any]], capital: float) -> str:
    if not rows:
        return "n=0"
    nets = [row["gross"] - actual_fee_pct(capital, row["position_size"]) for row in rows]
    wins = [value for value in nets if value > 0]
    losses = [value for value in nets if value <= 0]
    profit_factor = sum(wins) / abs(sum(losses)) if losses and sum(losses) else float("inf")
    fee_drags = [actual_fee_pct(capital, row["position_size"]) for row in rows]
    pf_text = "∞" if profit_factor == float("inf") else f"{profit_factor:.3f}"
    return (
        f"n={len(rows)} WR={len(wins) * 100 / len(rows):.1f}% "
        f"gross={avg([row['gross'] for row in rows]):+.3f}% "
        f"net={avg(nets):+.3f}% PF={pf_text} cost={avg(fee_drags):.3f}pp"
    )


def period_metrics(rows: list[dict[str, Any]], capital: float) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["signal_date"][:7]].append(row)
    parts = []
    for month, values in sorted(grouped.items()):
        nets = [row["gross"] - actual_fee_pct(capital, row["position_size"]) for row in values]
        parts.append(f"{month}:n={len(values)},WR={sum(value > 0 for value in nets) * 100 / len(nets):.0f}%,均={avg(nets):+.2f}%")
    return "；".join(parts)


def exit_metrics(rows: list[dict[str, Any]], capital: float) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["exit"]].append(row)
    parts = []
    for reason, values in sorted(grouped.items(), key=lambda item: -len(item[1])):
        nets = [row["gross"] - actual_fee_pct(capital, row["position_size"]) for row in values]
        parts.append(f"{reason}:n={len(values)},均={avg(nets):+.2f}%")
    return "；".join(parts)


def main() -> int:
    db = SessionLocal()
    try:
        observations = load_observations(db)
        print("=== 三条方向研究（实际费率重定价） ===")
        print("假设：佣金万分之0.85/单边、单边最低1元、卖出印花税万分之5；持仓按账户资金×回测 position_size；回测窗口为当前策略半年窗口。")
        for capital in CAPITAL_SCENARIOS:
            print(f"\n--- 账户资金 {capital:,.0f} 元；实际费率情景 ---")
            for strategy_id, config in TARGETS.items():
                rows = observations.get(strategy_id, [])
                anchor_rows = selected(rows, anchor_value=config["anchor"])
                regime_rows = selected(rows, anchor_value=config["anchor"], regime=config["regime"])
                score_rows = selected(rows, anchor_value=config["anchor"], high_score=True)
                triple_rows = selected(rows, anchor_value=config["anchor"], regime=config["regime"], high_score=True)
                print(f"\n{config['name']} [{strategy_id}]")
                print(f"主买点 {config['anchor']}: {metrics(anchor_rows, capital)}")
                print(f"主买点+周期 {config['regime']}: {metrics(regime_rows, capital)}")
                print(f"主买点+分数85+: {metrics(score_rows, capital)}")
                print(f"三者交集: {metrics(triple_rows, capital)}")
                if capital == 50_000.0:
                    print(f"三者交集按月份: {period_metrics(triple_rows, capital)}")
                    print(f"三者交集退出: {exit_metrics(triple_rows, capital)}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
