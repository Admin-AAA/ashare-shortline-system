from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
from sqlalchemy import select


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY_REGISTRY  # noqa: E402
from app.models.tables import DailyKbar  # noqa: E402
from app.services.backtest.challenger_decision import (  # noqa: E402
    PerformanceSnapshot,
    evaluate_challenger,
)
from app.services.entry_policy import (  # noqa: E402
    EntryPolicy,
    NEXT_TRADING_DAY_OPEN,
    entry_policy_for_mode,
)
from app.services.outcome_simulation import _simulate_outcome  # noqa: E402


WINDOWS = ("W1", "W2", "W3")
HORIZON_DAYS = 5
SETTLED_LABELS = frozenset({"success", "failed"})


def _registered_entry_policy(version: str) -> EntryPolicy:
    entry = DEFAULT_EXECUTION_POLICY_REGISTRY.get("a-share-shortline", version).entry
    return EntryPolicy(
        policy_id=entry.policy_id,
        price_source=entry.price_source.value,
        max_entry_trade_days=entry.max_entry_trade_days,
        allow_target_touch=entry.allow_target_touch,
        fallback_to_open_when_targets_miss=entry.fallback_to_open_when_targets_miss,
    )


CHAMPION_ENTRY = _registered_entry_policy("5.0.0")


def _load_panel(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict) or not isinstance(payload.get("days"), list):
        raise ValueError(f"invalid entry panel: {path}")
    return payload


def _selected_rows(panel: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in panel["days"]:
        signal_date = str(day["signal_date"])
        cards = {str(card.get("code")): dict(card) for card in day.get("cards") or []}
        for code in day.get("selected") or []:
            normalized_code = str(code)
            card = cards.get(normalized_code)
            if card is None:
                raise ValueError(f"selected card missing: {signal_date}:{normalized_code}")
            rows.append({"signal_date": signal_date, "code": normalized_code, "card": card})
    return rows


def _load_bars(db, rows: list[dict[str, Any]]) -> dict[str, list[DailyKbar]]:
    codes = sorted({row["code"] for row in rows})
    start = min(row["signal_date"] for row in rows)
    end = (
        date.fromisoformat(max(row["signal_date"] for row in rows))
        + timedelta(days=30)
    ).isoformat()
    bars = db.scalars(
        select(DailyKbar)
        .where(
            DailyKbar.code.in_(codes),
            DailyKbar.trade_date >= start,
            DailyKbar.trade_date <= end,
        )
        .order_by(DailyKbar.code, DailyKbar.trade_date)
    ).all()
    grouped: dict[str, list[DailyKbar]] = defaultdict(list)
    for bar in bars:
        grouped[str(bar.code)].append(bar)
    return grouped


def _feature_snapshot(card: dict[str, Any]) -> dict[str, Any]:
    snapshot = dict(card.get("feature_snapshot") or {})
    for key in (
        "entry_mode",
        "final_score",
        "market_discipline",
        "stop_loss",
        "take_profit",
        "setup_type",
    ):
        if card.get(key) is not None:
            snapshot[key] = card[key]
    return snapshot


def _recommendation(row: dict[str, Any]) -> SimpleNamespace:
    card = row["card"]
    return SimpleNamespace(
        recommendation_id=f"entry-panel:{row['signal_date']}:{row['code']}",
        code=row["code"],
        trade_date=row["signal_date"],
        recommend_price=float(card.get("recommend_price") or 0),
        bucket=str(card.get("bucket") or ""),
        final_score=float(card.get("final_score") or 0),
        recommended_at=None,
        feature_snapshot_json="{}",
    )


def _best_strategy_id(card: dict[str, Any]) -> str:
    signals = [
        signal
        for signal in card.get("strategy_signals") or []
        if isinstance(signal, dict) and not signal.get("hard_veto")
    ]
    if not signals:
        return ""
    best = max(signals, key=lambda signal: float(signal.get("score") or 0))
    return str(best.get("strategy_id") or "")


def _simulate_row(
    row: dict[str, Any],
    code_bars: list[DailyKbar],
    *,
    challenger: bool,
    challenger_best_strategies: frozenset[str],
) -> dict[str, Any]:
    card = row["card"]
    snapshot = _feature_snapshot(card)
    current_entry = entry_policy_for_mode(
        snapshot.get("entry_mode"),
        legacy_strategy_id=card.get("bucket"),
    )
    if current_entry.policy_id == NEXT_TRADING_DAY_OPEN.policy_id:
        selected_entry = current_entry
    elif challenger and _best_strategy_id(card) in challenger_best_strategies:
        selected_entry = NEXT_TRADING_DAY_OPEN
    else:
        selected_entry = CHAMPION_ENTRY
    dates = [str(bar.trade_date) for bar in code_bars]
    simulation = _simulate_outcome(
        _recommendation(row),
        code_bars,
        dates,
        HORIZON_DAYS,
        feature_snapshot=snapshot,
        entry_policy=selected_entry,
    )
    if simulation is None:
        return {
            "signal_date": row["signal_date"],
            "code": row["code"],
            "selected": True,
            "settled": False,
            "executed": False,
            "net_return_pct": None,
            "position_size": 0.0,
            "exit_reason": "missing_outcome_bars",
            "setup_type": str(card.get("setup_type") or ""),
            "entry_mode": str(card.get("entry_mode") or "target_touch"),
            "best_strategy_id": _best_strategy_id(card),
        }
    label = str(simulation.get("outcome_label") or "")
    net_return = simulation.get("net_return_pct")
    return {
        "signal_date": row["signal_date"],
        "code": row["code"],
        "selected": True,
        "settled": label in SETTLED_LABELS and net_return is not None,
        "executed": bool(simulation.get("entry_triggered")),
        "net_return_pct": float(net_return) if net_return is not None else None,
        "position_size": float(simulation.get("position_size") or 0),
        "entry_date": simulation.get("entry_date"),
        "entry_price": simulation.get("entry_price"),
        "entry_policy_id": simulation.get("entry_policy_id"),
        "exit_reason": simulation.get("exit_reason"),
        "setup_type": str(card.get("setup_type") or ""),
        "entry_mode": str(card.get("entry_mode") or "target_touch"),
        "best_strategy_id": _best_strategy_id(card),
    }


def _group_diagnostics(
    champion: list[dict[str, Any]],
    challenger: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for field in ("best_strategy_id", "setup_type"):
        values = sorted({str(row.get(field) or "") for row in champion})
        groups: list[dict[str, Any]] = []
        for value in values:
            indexes = [
                index
                for index, row in enumerate(champion)
                if str(row.get(field) or "") == value
            ]
            current = [champion[index] for index in indexes]
            proposed = [challenger[index] for index in indexes]
            current_settled = [row for row in current if row["settled"]]
            proposed_settled = [row for row in proposed if row["settled"]]
            current_sum = sum(float(row["net_return_pct"]) for row in current_settled)
            proposed_sum = sum(float(row["net_return_pct"]) for row in proposed_settled)
            groups.append(
                {
                    "value": value,
                    "selected_count": len(indexes),
                    "champion_n": len(current_settled),
                    "challenger_n": len(proposed_settled),
                    "champion_sum_net_pct": current_sum,
                    "challenger_sum_net_pct": proposed_sum,
                    "delta_sum_net_pct": proposed_sum - current_sum,
                    "added_entries": sum(
                        not before["executed"] and after["settled"]
                        for before, after in zip(current, proposed, strict=True)
                    ),
                }
            )
        result[field] = groups
    return result


def _metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [row for row in records if row["settled"]]
    returns = np.array([float(row["net_return_pct"]) for row in settled], dtype=float)
    if not len(returns):
        raise ValueError("no settled executed records")
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    daily: dict[str, list[float]] = defaultdict(list)
    for row in settled:
        daily[row["signal_date"]].append(float(row["net_return_pct"]))
    daily_returns = np.array(
        [float(np.mean(values)) for _, values in sorted(daily.items())],
        dtype=float,
    )
    curve = np.cumsum(daily_returns)
    running_peak = np.maximum.accumulate(np.concatenate(([0.0], curve)))
    drawdowns = np.concatenate(([0.0], curve)) - running_peak
    selected_count = len(records)
    return {
        "selected_count": selected_count,
        "n": int(len(returns)),
        "win_rate_pct": float((returns > 0).mean() * 100),
        "average_net_return_pct": float(returns.mean()),
        "cumulative_net_return_pct": float(returns.sum()),
        "profit_factor": float(wins.sum() / max(abs(losses.sum()), 1e-9)),
        "max_drawdown_pct": float(drawdowns.min()),
        "average_win_pct": float(wins.mean()) if len(wins) else 0.0,
        "average_loss_pct": float(losses.mean()) if len(losses) else 0.0,
        "coverage_pct": float(len(returns) / max(selected_count, 1) * 100),
        "capital_utilization_pct": float(
            sum(float(row["position_size"]) for row in settled)
            / max(selected_count, 1)
            * 100
        ),
        "missed_count": int(sum(not row["executed"] for row in records)),
        "pending_count": int(sum(row["executed"] and not row["settled"] for row in records)),
    }


def _paired_daily_ci(
    champion: list[dict[str, Any]],
    challenger: list[dict[str, Any]],
    *,
    draws: int = 4000,
    seed: int = 7,
) -> dict[str, float]:
    def daily_values(records: list[dict[str, Any]]) -> dict[str, float]:
        grouped: dict[str, list[float]] = defaultdict(list)
        for row in records:
            value = float(row["net_return_pct"]) if row["settled"] else 0.0
            grouped[row["signal_date"]].append(value)
        return {day: float(np.mean(values)) for day, values in grouped.items()}

    champion_daily = daily_values(champion)
    challenger_daily = daily_values(challenger)
    days = sorted(set(champion_daily) | set(challenger_daily))
    delta = np.array(
        [challenger_daily.get(day, 0.0) - champion_daily.get(day, 0.0) for day in days],
        dtype=float,
    )
    rng = np.random.default_rng(seed)
    sampled = np.array(
        [delta[rng.integers(0, len(delta), len(delta))].mean() for _ in range(draws)],
        dtype=float,
    )
    return {
        "mean_delta_pct": float(delta.mean()),
        "ci95_low_pct": float(np.quantile(sampled, 0.025)),
        "ci95_high_pct": float(np.quantile(sampled, 0.975)),
    }


def _decision(champion: dict[str, Any], challenger: dict[str, Any]) -> dict[str, Any]:
    def snapshot(metrics: dict[str, Any]) -> PerformanceSnapshot:
        return PerformanceSnapshot(
            average_net_return_pct=metrics["average_net_return_pct"],
            cumulative_net_return_pct=metrics["cumulative_net_return_pct"],
            win_rate_pct=metrics["win_rate_pct"],
            profit_factor=metrics["profit_factor"],
            max_drawdown_pct=metrics["max_drawdown_pct"],
        )

    return evaluate_challenger(snapshot(champion), snapshot(challenger)).to_dict()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--challenger-best-strategies",
        required=True,
        help="逗号分隔；这些策略作为最高分来源时，候选组改用次日开盘入场",
    )
    args = parser.parse_args()
    challenger_best_strategies = frozenset(
        value.strip()
        for value in args.challenger_best_strategies.split(",")
        if value.strip()
    )

    output: dict[str, Any] = {
        "method": "production causal outcome simulation over v27 selected entry panels",
        "horizon_days": HORIZON_DAYS,
        "champion_execution_policy": "5.0.0",
        "challenger_best_strategies": sorted(challenger_best_strategies),
        "windows": {},
    }
    db = SessionLocal()
    try:
        for window in WINDOWS:
            panel_path = args.panel_dir / f"entry_panel_{window}.json"
            panel = _load_panel(panel_path)
            rows = _selected_rows(panel)
            bars_by_code = _load_bars(db, rows)
            champion_records = [
                _simulate_row(
                    row,
                    bars_by_code.get(row["code"], []),
                    challenger=False,
                    challenger_best_strategies=challenger_best_strategies,
                )
                for row in rows
            ]
            challenger_records = [
                _simulate_row(
                    row,
                    bars_by_code.get(row["code"], []),
                    challenger=True,
                    challenger_best_strategies=challenger_best_strategies,
                )
                for row in rows
            ]
            champion_metrics = _metrics(champion_records)
            challenger_metrics = _metrics(challenger_records)
            output["windows"][window] = {
                "start": panel["days"][0]["signal_date"],
                "end": panel["days"][-1]["signal_date"],
                "policy_note": panel.get("policy_note"),
                "champion": champion_metrics,
                "challenger": challenger_metrics,
                "paired_daily_bootstrap": _paired_daily_ci(
                    champion_records,
                    challenger_records,
                ),
                "decision": _decision(champion_metrics, challenger_metrics),
                "added_entries": [
                    challenger
                    for champion, challenger in zip(champion_records, challenger_records, strict=True)
                    if not champion["executed"] and challenger["settled"]
                ],
                "group_diagnostics": _group_diagnostics(
                    champion_records,
                    challenger_records,
                ),
            }
            print(
                f"[{window}] champion n={champion_metrics['n']} "
                f"WR={champion_metrics['win_rate_pct']:.1f}% "
                f"sum={champion_metrics['cumulative_net_return_pct']:+.2f} | "
                f"challenger n={challenger_metrics['n']} "
                f"WR={challenger_metrics['win_rate_pct']:.1f}% "
                f"sum={challenger_metrics['cumulative_net_return_pct']:+.2f} "
                f"decision={output['windows'][window]['decision']['decision']}",
                flush=True,
            )
    finally:
        db.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"saved: {args.out}", flush=True)


if __name__ == "__main__":
    main()
