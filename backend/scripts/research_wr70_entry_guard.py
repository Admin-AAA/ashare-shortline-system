from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal  # noqa: E402
from scripts.research_entry_mode_three_window import (  # noqa: E402
    WINDOWS,
    _best_strategy_id,
    _decision,
    _load_bars,
    _load_panel,
    _metrics,
    _paired_daily_ci,
    _selected_rows,
    _simulate_row,
)


CURRENT_NEXT_OPEN_STRATEGIES = frozenset(
    {"chao-gu-yang-jia-skill", "daily-stock-analysis-skill"}
)
TARGET_WIN_RATE_PCT = 70.0
TARGET_PROFIT_FACTOR = 1.2
MIN_AFFECTED_SIGNALS = 20


@dataclass(frozen=True)
class Guard:
    guard_id: str
    hypothesis: str
    predicate: Callable[[dict[str, Any]], bool]


def _feature(row: dict[str, Any], name: str) -> float | None:
    card = row["card"]
    if name in card and card[name] is not None:
        return float(card[name])
    value = (card.get("feature_snapshot") or {}).get(name)
    return None if value is None else float(value)


def _best_signal(row: dict[str, Any]) -> dict[str, Any]:
    signals = [
        signal
        for signal in row["card"].get("strategy_signals") or []
        if isinstance(signal, dict) and not signal.get("hard_veto")
    ]
    if not signals:
        return {}
    return max(signals, key=lambda signal: float(signal.get("score") or 0.0))


def _non_veto_signal_count(row: dict[str, Any]) -> int:
    return sum(
        isinstance(signal, dict) and not signal.get("hard_veto")
        for signal in row["card"].get("strategy_signals") or []
    )


def _numeric_guards() -> list[Guard]:
    guards: list[Guard] = []
    lower_bounds = {
        "final_score": (70.0, 72.0, 74.0, 76.0, 78.0, 80.0),
        "evidence_quality_score": (20.0, 30.0, 40.0, 50.0),
        "data_authenticity": (0.70, 0.75, 0.80, 0.85),
        "pct": (-2.0, 0.0, 1.0, 2.0),
        "eff20": (0.0, 0.10, 0.20, 0.30),
    }
    upper_bounds = {
        "turnover_rate": (4.0, 5.0, 6.0, 7.0, 8.0),
        "amount_yi": (8.0, 10.0, 12.0, 15.0, 20.0),
        "chg20": (10.0, 15.0, 18.0, 20.0, 22.0),
        "chg60": (20.0, 25.0, 33.0, 40.0, 50.0),
        "pos60": (60.0, 70.0, 80.0, 87.0),
        "rsi_12": (50.0, 55.0, 60.0, 65.0, 70.0),
        "dist20": (0.0, 3.0, 6.0, 10.0, 15.0),
        "ivol20_pct": (0.30, 0.45, 0.60, 0.70, 0.80),
        "ivol_dn20_pct": (0.30, 0.45, 0.60, 0.70, 0.80),
        "max_dd60": (-25.0, -22.0, -20.0, -18.0),
    }
    for field, thresholds in lower_bounds.items():
        for threshold in thresholds:
            guards.append(
                Guard(
                    f"{field}_ge_{threshold:g}",
                    f"{field} 较高代表信号日质量更强，预期提高 WR 与 PF。",
                    lambda row, field=field, threshold=threshold: (
                        _feature(row, field) is not None
                        and float(_feature(row, field)) >= threshold
                    ),
                )
            )
    for field, thresholds in upper_bounds.items():
        for threshold in thresholds:
            guards.append(
                Guard(
                    f"{field}_lt_{threshold:g}",
                    f"{field} 较低代表追涨与波动风险较小，预期提高 WR 与最大回撤。",
                    lambda row, field=field, threshold=threshold: (
                        _feature(row, field) is not None
                        and float(_feature(row, field)) < threshold
                    ),
                )
            )
    return guards


def _structural_guards() -> list[Guard]:
    return [
        Guard(
            "support_any",
            "信号日获得 MA5 或 MA10 支撑时，T+1 延续概率更高，预期提高 WR 与 PF。",
            lambda row: bool(row["card"].get("support_ma5") or row["card"].get("support_ma10")),
        ),
        Guard(
            "support_both",
            "信号日同时获得 MA5 与 MA10 支撑时，T+1 延续概率更高，预期提高 WR 与最大回撤。",
            lambda row: bool(row["card"].get("support_ma5") and row["card"].get("support_ma10")),
        ),
        Guard(
            "consensus_ge_2",
            "至少两个非否决策略同时背书可减少单一规则误判，预期提高 WR 与 PF。",
            lambda row: _non_veto_signal_count(row) >= 2,
        ),
        Guard(
            "consensus_ge_3",
            "至少三个非否决策略同时背书可减少单一规则误判，预期提高 WR 与 PF。",
            lambda row: _non_veto_signal_count(row) >= 3,
        ),
        Guard(
            "best_signal_confidence_ge_0_7",
            "最高分策略信号的 confidence 达到 0.7 时，T+1 延续概率更高，预期提高 WR 与 PF。",
            lambda row: float(_best_signal(row).get("confidence") or 0.0) >= 0.70,
        ),
        Guard(
            "best_signal_setup_quality_ge_70",
            "最高分策略信号的 setup_quality 达到 70 时，形态完整度更高，预期提高 WR 与 PF。",
            lambda row: float(_best_signal(row).get("setup_quality") or 0.0) >= 70.0,
        ),
    ]


def _day_feature(row: dict[str, Any], name: str) -> float | None:
    snapshot = row.get("market_snapshot") or {}
    if name == "up_ratio":
        breadth = snapshot.get("breadth") or {}
        value = breadth.get("up_ratio")
        if value is None:
            up = float(snapshot.get("up_count") or 0.0)
            down = float(snapshot.get("down_count") or 0.0)
            return None if up + down <= 0.0 else up / (up + down)
        return float(value)
    value = snapshot.get(name)
    return None if value is None else float(value)


def _day_guards() -> list[Guard]:
    guards: list[Guard] = []
    for threshold in (0.25, 0.30, 0.35, 0.40, 0.45, 0.50):
        guards.append(
            Guard(
                f"day_up_ratio_ge_{threshold:g}",
                "信号日上涨家数占比较高时，T+1 承接更充分，预期提高 WR 与最大回撤。",
                lambda row, threshold=threshold: (
                    _day_feature(row, "up_ratio") is not None
                    and float(_day_feature(row, "up_ratio")) >= threshold
                ),
            )
        )
    for threshold in (0.50, 0.55, 0.60, 0.65, 0.70):
        guards.append(
            Guard(
                f"day_up_ratio_lt_{threshold:g}",
                "信号日上涨家数占比不过热时，T+1 回吐风险较小，预期提高 WR 与 PF。",
                lambda row, threshold=threshold: (
                    _day_feature(row, "up_ratio") is not None
                    and float(_day_feature(row, "up_ratio")) < threshold
                ),
            )
        )
    for threshold in (2.0, 5.0, 8.0, 12.0, 20.0):
        guards.append(
            Guard(
                f"day_limit_down_lt_{threshold:g}",
                "信号日跌停数量较少时，系统性风险较低，预期提高 WR 与最大回撤。",
                lambda row, threshold=threshold: (
                    _day_feature(row, "limit_down_count") is not None
                    and float(_day_feature(row, "limit_down_count")) < threshold
                ),
            )
        )
    for threshold in (20.0, 30.0, 40.0, 50.0, 60.0, 80.0):
        guards.append(
            Guard(
                f"day_limit_up_ge_{threshold:g}",
                "信号日涨停数量较多时，短线情绪具有延续基础，预期提高 WR 与 PF。",
                lambda row, threshold=threshold: (
                    _day_feature(row, "limit_up_count") is not None
                    and float(_day_feature(row, "limit_up_count")) >= threshold
                ),
            )
        )
    by_id = {guard.guard_id: guard for guard in guards}
    for ratio_id in (
        "day_up_ratio_ge_0.25",
        "day_up_ratio_ge_0.3",
        "day_up_ratio_ge_0.35",
        "day_up_ratio_ge_0.4",
    ):
        for risk_id in (
            "day_limit_down_lt_5",
            "day_limit_down_lt_8",
            "day_limit_down_lt_12",
        ):
            left = by_id[ratio_id]
            right = by_id[risk_id]
            guards.append(
                Guard(
                    f"{ratio_id}__{risk_id}",
                    f"{left.hypothesis} 同时要求 {right.hypothesis}",
                    lambda row, left=left, right=right: (
                        left.predicate(row) and right.predicate(row)
                    ),
                )
            )
    return guards


def _strategy_groups() -> dict[str, frozenset[str]]:
    return {
        "all": frozenset({"*"}),
        "chao_dsa": frozenset(
            {"chao-gu-yang-jia-skill", "daily-stock-analysis-skill"}
        ),
        "chao_dsa_chen": frozenset(
            {
                "chao-gu-yang-jia-skill",
                "daily-stock-analysis-skill",
                "chen-xiaoqun-skill",
            }
        ),
    }


def _pair_guards(guards: list[Guard]) -> list[Guard]:
    by_id = {guard.guard_id: guard for guard in guards}
    structural_ids = (
        "support_any",
        "support_both",
        "consensus_ge_2",
        "best_signal_confidence_ge_0_7",
        "best_signal_setup_quality_ge_70",
    )
    numeric_prefixes = (
        "final_score_",
        "pct_",
        "eff20_",
        "turnover_rate_",
        "amount_yi_",
        "chg20_",
        "chg60_",
        "pos60_",
        "rsi_12_",
        "dist20_",
        "ivol20_pct_",
        "max_dd60_",
    )
    numeric_ids = [
        guard_id
        for guard_id in by_id
        if guard_id.startswith(numeric_prefixes)
    ]
    pairs: list[Guard] = []
    for left_id in structural_ids:
        left = by_id[left_id]
        for right_id in numeric_ids:
            right = by_id[right_id]
            pairs.append(
                Guard(
                    f"{left_id}__{right_id}",
                    f"{left.hypothesis} 同时要求 {right.hypothesis}",
                    lambda row, left=left, right=right: (
                        left.predicate(row) and right.predicate(row)
                    ),
                )
            )
    return pairs


def _apply_guard(
    rows: list[dict[str, Any]],
    records: list[dict[str, Any]],
    *,
    strategy_ids: frozenset[str],
    guard: Guard,
) -> tuple[list[dict[str, Any]], int, int]:
    output: list[dict[str, Any]] = []
    affected = 0
    rejected = 0
    for row, record in zip(rows, records, strict=True):
        if "*" not in strategy_ids and _best_strategy_id(row["card"]) not in strategy_ids:
            output.append(record)
            continue
        affected += 1
        if guard.predicate(row):
            output.append(record)
            continue
        rejected += 1
        output.append(
            {
                **record,
                "settled": False,
                "executed": False,
                "net_return_pct": None,
                "position_size": 0.0,
                "exit_reason": f"entry_guard:{guard.guard_id}",
            }
        )
    return output, affected, rejected


def _hard_target(metrics: dict[str, Any]) -> bool:
    return bool(
        metrics["n"] >= MIN_AFFECTED_SIGNALS
        and metrics["win_rate_pct"] >= TARGET_WIN_RATE_PCT
        and metrics["profit_factor"] >= TARGET_PROFIT_FACTOR
        and metrics["cumulative_net_return_pct"] > 0.0
    )


def _metric_payload(
    records: list[dict[str, Any]],
    *,
    panel_days: int,
) -> dict[str, Any]:
    if not any(row["settled"] for row in records):
        return {
            "selected_count": len(records),
            "n": 0,
            "win_rate_pct": 0.0,
            "average_net_return_pct": 0.0,
            "cumulative_net_return_pct": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0.0,
            "average_win_pct": 0.0,
            "average_loss_pct": 0.0,
            "coverage_pct": 0.0,
            "capital_utilization_pct": 0.0,
            "missed_count": len(records),
            "pending_count": 0,
            "active_day_coverage_pct": 0.0,
        }
    metrics = _metrics(records)
    active_days = len({row["signal_date"] for row in records if row["settled"]})
    metrics["active_day_coverage_pct"] = active_days / max(panel_days, 1) * 100.0
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--guard-family",
        choices=("entry", "day"),
        default="entry",
    )
    args = parser.parse_args()

    panels = {
        window: _load_panel(args.panel_dir / f"entry_panel_{window}.json")
        for window in WINDOWS
    }
    rows_by_window: dict[str, list[dict[str, Any]]] = {}
    for window, panel in panels.items():
        market_by_date = {
            str(day["signal_date"]): dict(day.get("market_snapshot") or {})
            for day in panel["days"]
        }
        rows_by_window[window] = [
            {**row, "market_snapshot": market_by_date[row["signal_date"]]}
            for row in _selected_rows(panel)
        ]
    all_rows = [row for rows in rows_by_window.values() for row in rows]
    db = SessionLocal()
    try:
        bars_by_code = _load_bars(db, all_rows)
        baseline_records = {
            window: [
                _simulate_row(
                    row,
                    bars_by_code.get(row["code"], []),
                    challenger=True,
                    challenger_best_strategies=CURRENT_NEXT_OPEN_STRATEGIES,
                )
                for row in rows
            ]
            for window, rows in rows_by_window.items()
        }
    finally:
        db.close()

    baseline = {
        window: _metric_payload(
            records,
            panel_days=len(panels[window]["days"]),
        )
        for window, records in baseline_records.items()
    }
    candidates: list[dict[str, Any]] = []
    best_failures: list[dict[str, Any]] = []
    if args.guard_family == "day":
        single_guards = _day_guards()
        pair_guards: list[Guard] = []
        groups = {"all": frozenset({"*"})}
    else:
        single_guards = [*_numeric_guards(), *_structural_guards()]
        pair_guards = _pair_guards(single_guards)
        groups = _strategy_groups()
    scanned_parameter_count = 0
    for group_id, strategy_ids in groups.items():
        guards = [*single_guards, *pair_guards] if group_id == "all" else single_guards
        for guard in guards:
            scanned_parameter_count += 1
            windows: dict[str, Any] = {}
            eligible = True
            positive_all = True
            target_all = True
            minimum_wr = 100.0
            for window in WINDOWS:
                candidate_records, affected, rejected = _apply_guard(
                    rows_by_window[window],
                    baseline_records[window],
                    strategy_ids=strategy_ids,
                    guard=guard,
                )
                metrics = _metric_payload(
                    candidate_records,
                    panel_days=len(panels[window]["days"]),
                )
                bootstrap = _paired_daily_ci(
                    baseline_records[window],
                    candidate_records,
                )
                decision = _decision(baseline[window], metrics)
                eligible = eligible and affected >= MIN_AFFECTED_SIGNALS
                positive_all = positive_all and bootstrap["mean_delta_pct"] > 0.0
                target_all = target_all and _hard_target(metrics)
                minimum_wr = min(minimum_wr, float(metrics["win_rate_pct"]))
                windows[window] = {
                    "affected_signals": affected,
                    "rejected_signals": rejected,
                    "metrics": metrics,
                    "paired_daily_bootstrap": bootstrap,
                    "decision": decision,
                }
            payload = {
                "candidate_id": f"{group_id}:{guard.guard_id}",
                "strategy_ids": sorted(strategy_ids),
                "guard_id": guard.guard_id,
                "hypothesis": guard.hypothesis,
                "eligible": eligible,
                "positive_all_windows": positive_all,
                "hard_target_all_windows": target_all,
                "minimum_window_wr_pct": minimum_wr,
                "windows": windows,
            }
            if eligible and positive_all and target_all:
                candidates.append(payload)
            elif eligible and positive_all:
                best_failures.append(payload)

    candidates.sort(key=lambda item: item["minimum_window_wr_pct"], reverse=True)
    best_failures.sort(key=lambda item: item["minimum_window_wr_pct"], reverse=True)
    output = {
        "method": "path A v27 selected cards with production causal outcome simulation",
        "guard_family": args.guard_family,
        "current_next_open_strategies": sorted(CURRENT_NEXT_OPEN_STRATEGIES),
        "minimum_affected_signals_per_window": MIN_AFFECTED_SIGNALS,
        "baseline": baseline,
        "passing_candidates": candidates,
        "best_positive_failures": best_failures[:5],
        "scanned_parameter_count": scanned_parameter_count,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps({
        "baseline": {
            window: {
                "n": metrics["n"],
                "wr": round(metrics["win_rate_pct"], 2),
                "pf": round(metrics["profit_factor"], 3),
                "sum": round(metrics["cumulative_net_return_pct"], 2),
            }
            for window, metrics in baseline.items()
        },
        "passing_candidates": len(candidates),
        "best_candidate": candidates[0]["candidate_id"] if candidates else None,
        "best_positive_failure": (
            best_failures[0]["candidate_id"] if best_failures else None
        ),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
