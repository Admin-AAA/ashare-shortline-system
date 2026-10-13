"""Paired path-A replay for phase hard-veto shadowing.

The production rule pipeline is rebuilt once per window. The strategy seam
evaluates the same feature contexts with the historical first-phase-only
behavior and the repaired behavior, so both arms share every other input.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
from unittest.mock import patch

import numpy as np
from sqlalchemy import select

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from app.db.session import SessionLocal
from app.engine import strategy as strategy_module
from app.engine.evaluator import evaluate
from app.engine.strategy_runner import run_strategy as repaired_run_strategy
from app.engine.backtest import ExecutionPolicy
from app.engine.production_policy import (
    DEFAULT_PRODUCTION_SELECTION_POLICY,
    SelectionPolicyContext,
    evaluate_selection_policy,
)
from app.models.tables import CapitalFlow
from app.services.auxiliary import application as auxiliary
from app.services.backtest.candidate_snapshot_builder import (
    ProductionRuleReplayCandidateBuilder,
    _pre_policy_card,
)
from app.services.backtest.challenger_decision import PerformanceSnapshot, evaluate_challenger
from app.services.backtest.policy_experiment import (
    ScoredCandidateDay,
    ScoredCandidateSnapshot,
    _simulate_card,
    _thaw_json,
)
from app.services.backtest.stock_metadata import StockMetadataHistoryProvider
from app.services.outcome_simulation import _resolve_position_size
from app.services.runs.stages.adapters import ProductionRecommendationAssembly
from app.services.runs.stages.adapters import ProductionStrategyEvaluation

WINDOWS = {
    "W1": ("2025-01-15", "2026-01-16"),
    "W2": ("2026-01-17", "2026-07-30"),
    "W3": ("2026-08-01", "2026-09-13"),
}


def legacy_run_strategy(skill, stock, main_industries, skip_universe_check=False):
    """Reproduce the historical ``break`` after the first matching phase."""
    contract = _thaw_json(skill.contract or {})
    context = dict(stock)
    industry = stock.get("industry")
    context["is_main_theme"] = bool(
        stock.get("is_main_theme")
        or (industry in set(main_industries) and industry != "未分类")
    )
    phases = list(contract.get("phases") or ())
    contract["phases"] = [
        rule for rule in phases if evaluate(str(rule.get("when", "")), context)
    ][:1]
    return repaired_run_strategy(
        replace(skill, contract=contract),
        stock,
        main_industries,
        skip_universe_check=skip_universe_check,
    )


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".partial")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")
    pending.replace(path)


def build_paired_snapshots(db, start: str, end: str):
    old_aux = auxiliary.load_auxiliary_feature_views
    original_evaluation = ProductionStrategyEvaluation.evaluate
    original_assembly = ProductionRecommendationAssembly.aggregate
    candidate_results_by_day: dict[str, tuple[dict[str, Any], ...]] = {}
    candidate_cards_by_day: dict[str, tuple[dict[str, Any], ...]] = {}
    flow_dates = tuple(
        str(value)
        for value in db.scalars(
            select(CapitalFlow.trade_date)
            .where(CapitalFlow.trade_date < end)
            .distinct()
            .order_by(CapitalFlow.trade_date)
        ).all()
    )

    def prior_aux(session, codes, *, trade_date):
        current = dict(old_aux(session, codes, trade_date=trade_date))
        index = bisect_left(flow_dates, str(trade_date))
        prior_date = flow_dates[index - 1] if index else None
        prior = old_aux(session, codes, trade_date=prior_date) if prior_date else {}
        current["capital_flows"] = dict(prior.get("capital_flows") or {})
        current["coverage"] = dict(current.get("coverage") or {})
        for key in tuple(current["coverage"]):
            if "capital" in key:
                current["coverage"].pop(key)
        for key, value in (prior.get("coverage") or {}).items():
            if "capital" in key:
                current["coverage"][key] = value
        return current

    def paired_evaluation(instance, **kwargs):
        with patch.object(strategy_module, "run_strategy", legacy_run_strategy):
            champion = original_evaluation(instance, **copy.deepcopy(kwargs))
        candidate = original_evaluation(instance, **copy.deepcopy(kwargs))
        signal_date = str((kwargs.get("snapshot") or {}).get("trade_date") or "")
        candidate_results_by_day[signal_date] = tuple(
            {
                **dict(entry),
                "review_skill_ids": [],
                "agent_review_disabled": True,
                "skill_reviews": [],
            }
            for entry in candidate[1]
        )
        return champion

    def paired_assembly(instance, **kwargs):
        signal_date = str((kwargs.get("snapshot") or {}).get("trade_date") or "")
        champion = original_assembly(instance, **copy.deepcopy(kwargs))
        candidate_kwargs = copy.deepcopy(kwargs)
        candidate_kwargs["candidate_results"] = candidate_results_by_day.get(signal_date, ())
        candidate = original_assembly(instance, **candidate_kwargs)
        candidate_cards_by_day[signal_date] = tuple(
            _pre_policy_card(dict(card)) for card in candidate[1]
        )
        return champion

    builder = ProductionRuleReplayCandidateBuilder(
        db,
        stock_metadata_provider=StockMetadataHistoryProvider(db),
        historical_replay=True,
    )
    with patch.object(auxiliary, "load_auxiliary_feature_views", prior_aux), patch.object(
        ProductionStrategyEvaluation, "evaluate", paired_evaluation
    ), patch.object(ProductionRecommendationAssembly, "aggregate", paired_assembly):
        champion = builder.build(
            start_date=start,
            end_date=end,
            execution_policy=ExecutionPolicy(),
            score_line=72.0,
            output_count=10,
        )
    candidate = ScoredCandidateSnapshot(
        source="production-rule-replay:phase-hard-veto-shadowing",
        days=tuple(
            ScoredCandidateDay(
                signal_date=day.signal_date,
                market_snapshot=day.market_snapshot,
                cards=candidate_cards_by_day.get(day.signal_date, ()),
                outcome_bars=day.outcome_bars,
            )
            for day in champion.days
        ),
    )
    return champion, candidate


def run_arm(snapshot: ScoredCandidateSnapshot) -> dict[str, Any]:
    rows = []
    active_dates: set[str] = set()
    execution_policy = ExecutionPolicy()
    for day in snapshot.days:
        cards = _thaw_json(day.cards)
        result = evaluate_selection_policy(
            cards,
            SelectionPolicyContext(72.0, 10, day.market_snapshot),
            DEFAULT_PRODUCTION_SELECTION_POLICY,
        )
        card_by_code = {str(card.get("code") or ""): card for card in cards}
        if result.recommendations:
            active_dates.add(day.signal_date)
        for selected in result.recommendations:
            code = str(selected.get("code") or "")
            card = card_by_code[code]
            trade = (_simulate_card(day, card, execution_policy) or {}).get("trade")
            if trade is None or not trade.executed:
                continue
            rows.append(
                {
                    "signal_date": day.signal_date,
                    "code": code,
                    "final_score": float(card.get("final_score") or 0.0),
                    "position_size": _resolve_position_size(dict(card)),
                    "net": float(trade.net_return_pct),
                    "entry": str(trade.entry_date),
                    "entry_price": float(trade.entry_price),
                    "exit": str(trade.exit_date),
                    "closes": {
                        str(bar.trade_date): float(bar.close)
                        for bar in day.outcome_bars.get(code, ())
                    },
                }
            )
    return {
        "rows": rows,
        "active_dates": sorted(active_dates),
        "dates": [day.signal_date for day in snapshot.days],
    }


def account_metrics(arm: dict[str, Any]) -> dict[str, Any]:
    rows = arm["rows"]
    dates = arm["dates"]
    cash = equity = peak = 1.0
    active = []
    admitted = []
    curve = []
    blocked = 0
    entries: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        entries.setdefault(row["entry"], []).append(row)
    calendar = sorted(set(dates) | {day for row in rows for day in row["closes"]})
    for day in calendar:
        opening_equity = equity
        for row in sorted(
            entries.get(day, ()), key=lambda item: (-item["final_score"], item["code"])
        ):
            allocation = min(cash, opening_equity * row["position_size"])
            if allocation <= 1e-12:
                blocked += 1
                continue
            lot = {**row, "allocation": allocation, "mark": allocation}
            cash -= allocation
            active.append(lot)
            admitted.append(lot)
        remaining = []
        for lot in active:
            if lot["exit"] == day:
                cash += lot["allocation"] * (1.0 + lot["net"] / 100.0)
                continue
            if day in lot["closes"]:
                lot["mark"] = lot["allocation"] * lot["closes"][day] / lot["entry_price"]
            remaining.append(lot)
        active = remaining
        equity = cash + sum(lot["mark"] for lot in active)
        peak = max(peak, equity)
        curve.append(
            {
                "date": day,
                "drawdown": (equity / peak - 1.0) * 100.0,
                "utilization": (equity - cash) / equity if equity else 0.0,
            }
        )

    nets = np.asarray([row["net"] for row in admitted], dtype=float)
    wins = nets[nets > 0]
    losses = nets[nets < 0]
    daily = {
        day: sum(
            row["allocation"] * row["net"]
            for row in admitted
            if row["signal_date"] == day
        )
        for day in dates
    }
    return {
        "n": int(len(nets)),
        "wr": float(100.0 * len(wins) / len(nets)) if len(nets) else 0.0,
        "avg_net": float(nets.mean()) if len(nets) else 0.0,
        "sum_net": float(nets.sum()),
        "pf": float(wins.sum() / abs(losses.sum())) if len(losses) else 0.0,
        "mdd": min((point["drawdown"] for point in curve), default=0.0),
        "cumulative": (equity - 1.0) * 100.0,
        "avg_win": float(wins.mean()) if len(wins) else None,
        "avg_loss": float(losses.mean()) if len(losses) else None,
        "coverage": 100.0 * len(arm["active_dates"]) / len(dates) if dates else 0.0,
        "utilization": (
            100.0 * float(np.mean([point["utilization"] for point in curve]))
            if curve
            else 0.0
        ),
        "cash_blocked": blocked,
        "daily": daily,
    }


def assess(champion: dict[str, Any], candidate: dict[str, Any], dates: list[str]):
    paired = np.asarray(
        [candidate["daily"][day] - champion["daily"][day] for day in dates], dtype=float
    )
    rng = np.random.default_rng(913)
    means = np.asarray(
        [rng.choice(paired, len(paired), replace=True).mean() for _ in range(5000)]
    )

    def snapshot(metrics):
        return PerformanceSnapshot(
            metrics["avg_net"],
            metrics["cumulative"],
            metrics["wr"],
            metrics["pf"],
            metrics["mdd"],
        )

    return {
        "champion": champion,
        "candidate": candidate,
        "paired_day_ci95": [
            float(np.quantile(means, 0.025)),
            float(np.quantile(means, 0.975)),
        ],
        "decision": evaluate_challenger(snapshot(champion), snapshot(candidate)).to_dict(),
        "sample_gate": champion["n"] >= 20 and candidate["n"] >= 20,
        "target_met": (
            candidate["wr"] >= 60.0
            and candidate["pf"] >= 1.2
            and candidate["sum_net"] > 0.0
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", choices=tuple(WINDOWS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start, end = WINDOWS[args.window]
    diff = subprocess.run(
        ["git", "diff", "--binary"], cwd=B, text=True, capture_output=True, check=True
    ).stdout
    identity = {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=B, text=True).strip(),
        "working_diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
        "policy": DEFAULT_PRODUCTION_SELECTION_POLICY.fingerprint,
        "window": [start, end],
        "capital_flow": "strictly-prior-day",
        "score": "final_score",
        "position": "_resolve_position_size",
        "regime": "policy-owned decision regime",
        "changed_rule": "continue scanning later hard-veto phases after first soft phase",
        "evidence_scope": "diagnostic path A; not live_forward",
    }
    write_json(args.output, {"status": "running", "identity": identity})
    with SessionLocal() as db:
        champion_snapshot, candidate_snapshot = build_paired_snapshots(db, start, end)
    champion = account_metrics(run_arm(champion_snapshot))
    candidate = account_metrics(run_arm(candidate_snapshot))
    result = {
        "status": "complete",
        "identity": identity,
        "window": args.window,
        "metrics": assess(
            champion, candidate, [day.signal_date for day in champion_snapshot.days]
        ),
    }
    write_json(args.output, result)
    fields = ("n", "wr", "avg_net", "sum_net", "pf", "mdd", "cumulative")
    summary = {"window": args.window}
    summary.update(
        {
            side: {key: result["metrics"][side][key] for key in fields}
            for side in ("champion", "candidate")
        }
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
