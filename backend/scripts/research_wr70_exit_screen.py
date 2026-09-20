from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.engine.backtest import BarInput  # noqa: E402
from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY  # noqa: E402
from app.services.backtest.policy_experiment import (  # noqa: E402
    ScoredCandidateDay,
    _simulate_card,
)
from app.services.outcome_simulation import _resolve_position_size  # noqa: E402
from scripts.research_entry_mode_three_window import (  # noqa: E402
    WINDOWS,
    _best_strategy_id,
    _decision,
    _load_bars,
    _load_panel,
    _metrics,
    _paired_daily_ci,
    _selected_rows,
)


CURRENT_NEXT_OPEN_STRATEGIES = frozenset(
    {"chao-gu-yang-jia-skill", "daily-stock-analysis-skill"}
)


def _bar_input(bar: Any) -> BarInput | None:
    prices = [float(getattr(bar, field) or 0.0) for field in ("open", "high", "low", "close")]
    if min(prices) <= 0.0:
        return None
    return BarInput(
        code=str(bar.code),
        trade_date=str(bar.trade_date),
        open=prices[0],
        high=prices[1],
        low=prices[2],
        close=prices[3],
        volume=float(bar.volume) if bar.volume is not None else None,
        tradable=float(bar.volume or 0.0) > 0.0,
    )


def _card_for_current_entry(row: dict[str, Any]) -> dict[str, Any]:
    card = dict(row["card"])
    if _best_strategy_id(card) in CURRENT_NEXT_OPEN_STRATEGIES:
        card["entry_mode"] = "next_open"
    return card


def _simulate_records(
    rows: list[dict[str, Any]],
    bars_by_code: dict[str, list[Any]],
    policy: Any,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in rows:
        signal_date = row["signal_date"]
        bars = []
        for raw_bar in bars_by_code.get(row["code"], []):
            if str(raw_bar.trade_date) <= signal_date:
                continue
            bar = _bar_input(raw_bar)
            if bar is not None:
                bars.append(bar)
            if len(bars) >= 9:
                break
        card = _card_for_current_entry(row)
        day = ScoredCandidateDay(
            signal_date=signal_date,
            market_snapshot={},
            cards=(card,),
            outcome_bars={row["code"]: tuple(bars)},
        )
        trade = (_simulate_card(day, card, policy) or {}).get("trade")
        settled = bool(
            trade is not None
            and trade.executed
            and not (
                str(trade.exit_reason or "").startswith("horizon_close")
                and int(trade.holding_days) < int(policy.horizon_days)
            )
        )
        records.append(
            {
                "signal_date": signal_date,
                "code": row["code"],
                "selected": True,
                "settled": settled,
                "executed": bool(trade is not None and trade.executed),
                "net_return_pct": float(trade.net_return_pct) if settled else None,
                "position_size": _resolve_position_size(card) if settled else 0.0,
                "exit_reason": str(trade.exit_reason or "") if trade is not None else "missing_bars",
            }
        )
    return records


def _variants() -> dict[str, Any]:
    base = DEFAULT_EXECUTION_POLICY
    variants = {"baseline": base}
    for threshold in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
        variants[f"take_profit_{threshold:g}"] = dataclasses.replace(
            base,
            take_profit=dataclasses.replace(
                base.take_profit,
                enabled=True,
                default_gain_pct=threshold,
            ),
        )
    for lock_gain in (0.2, 0.5, 1.0, 1.5, 2.0):
        variants[f"trailing_lock_{lock_gain:g}"] = dataclasses.replace(
            base,
            trailing=dataclasses.replace(
                base.trailing,
                minimum_lock_gain_pct=lock_gain,
            ),
        )
    return variants


def _metric_payload(records: list[dict[str, Any]], panel_days: int) -> dict[str, Any]:
    metrics = _metrics(records)
    metrics["active_day_coverage_pct"] = (
        len({row["signal_date"] for row in records if row["settled"]})
        / max(panel_days, 1)
        * 100.0
    )
    return metrics


def _hard_target(metrics: dict[str, Any]) -> bool:
    return bool(
        metrics["n"] >= 20
        and metrics["win_rate_pct"] >= 70.0
        and metrics["profit_factor"] >= 1.2
        and metrics["cumulative_net_return_pct"] > 0.0
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    panels = {
        window: _load_panel(args.panel_dir / f"entry_panel_{window}.json")
        for window in WINDOWS
    }
    rows_by_window = {window: _selected_rows(panel) for window, panel in panels.items()}
    all_rows = [row for rows in rows_by_window.values() for row in rows]
    db = SessionLocal()
    try:
        bars_by_code = _load_bars(db, all_rows)
    finally:
        db.close()

    variants = _variants()
    records: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(dict)
    metrics: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for variant_id, policy in variants.items():
        for window in WINDOWS:
            current_records = _simulate_records(rows_by_window[window], bars_by_code, policy)
            records[variant_id][window] = current_records
            metrics[variant_id][window] = _metric_payload(
                current_records,
                len(panels[window]["days"]),
            )

    candidates: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for variant_id in variants:
        if variant_id == "baseline":
            continue
        windows: dict[str, Any] = {}
        target_all = True
        gate_all = True
        for window in WINDOWS:
            candidate_metrics = metrics[variant_id][window]
            baseline_metrics = metrics["baseline"][window]
            bootstrap = _paired_daily_ci(
                records["baseline"][window],
                records[variant_id][window],
            )
            decision = _decision(baseline_metrics, candidate_metrics)
            target_all = target_all and _hard_target(candidate_metrics)
            gate_all = gate_all and decision["decision"] == "PRODUCTION"
            windows[window] = {
                "metrics": candidate_metrics,
                "paired_daily_bootstrap": bootstrap,
                "decision": decision,
            }
        payload = {
            "variant_id": variant_id,
            "hypothesis": "更早锁定盘中盈利可把回落单转为净盈利，预期提高 WR 与 PF。",
            "hard_target_all_windows": target_all,
            "production_gate_all_windows": gate_all,
            "windows": windows,
        }
        if target_all and gate_all:
            candidates.append(payload)
        else:
            failures.append(payload)

    output = {
        "method": "path A v27 selected cards with canonical pure execution simulation",
        "baseline_policy": DEFAULT_EXECUTION_POLICY.canonical_payload(),
        "baseline": metrics["baseline"],
        "passing_candidates": candidates,
        "falsified_candidates": failures,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    print(json.dumps({
        "baseline": {
            window: {
                "n": metrics["baseline"][window]["n"],
                "wr": round(metrics["baseline"][window]["win_rate_pct"], 2),
                "pf": round(metrics["baseline"][window]["profit_factor"], 3),
                "sum": round(metrics["baseline"][window]["cumulative_net_return_pct"], 2),
            }
            for window in WINDOWS
        },
        "passing_candidates": [item["variant_id"] for item in candidates],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
