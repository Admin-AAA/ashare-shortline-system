"""Rebuild the self-improve production chain from the current 20cm champion.

The script deliberately applies production entry rules before testing later
challengers.  It therefore prevents an exit candidate from being evaluated on
trades that the current production universe would never admit.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.engine.aggregator import STRUCTURAL_STRONG_HARD_VETO_REGIME  # noqa: E402
from app.models.tables import DailyKbar, Recommendation, RecommendationRun  # noqa: E402
from app.services.backtest.challenger_decision import (  # noqa: E402
    ChallengerDecision,
    PerformanceSnapshot,
    evaluate_challenger,
)
from app.services.official_sample_scope import official_sample_conditions  # noqa: E402
from app.services.outcome_simulation import (  # noqa: E402
    _resolve_exit_causal,
    _simulate_outcome,
    TRAILING_STOP_PROFIT_LOCK_RATIO,
)

SCORE_LINE_FLOOR = 72.0
NET_COST_PCT = 0.07
HORIZON = 5
PRODUCTION_BOARD_UNIVERSE = get_settings().formal_board_universe
CHAMPION_TRAILING_RATIO = TRAILING_STOP_PROFIT_LOCK_RATIO
CHALLENGER_TRAILING_RATIO = 0.90
OUTPUT_DIR = BACKEND_ROOT.parent / "output"


def _board(code: str) -> str:
    return "20cm" if str(code).startswith(("300", "301", "688", "689")) else "10cm"


def _in_production_universe(code: str) -> bool:
    universe = (PRODUCTION_BOARD_UNIVERSE or "all").lower()
    return universe in {"", "all"} or _board(code) == universe


def _loads(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _decision_regime(rec: Recommendation) -> str | None:
    snapshot = _loads(rec.feature_snapshot_json)
    score_line_meta = snapshot.get("score_line_meta") or {}
    value = score_line_meta.get("decision_regime")
    if value:
        return str(value)
    # Older recommendations predate score_line_meta.decision_regime, but each
    # row persisted card["market_snapshot"] in market_context_json.  The card
    # and apply_pool_rules received the same snapshot object, so this is a
    # decision-time fallback.  feature_snapshot_json.market_regime remains
    # inadmissible because later feature rebuilding can change it.
    market_context = _loads(rec.market_context_json)
    value = (
        market_context.get("market_regime")
        or market_context.get("regime")
        or (market_context.get("breadth") or {}).get("regime")
    )
    return str(value) if value else None


def _trailing_resolver(ratio: float):
    def resolver(holding_bars, stop_loss_price, take_profit_price, *, code="", entry_price=0.0):
        return _resolve_exit_causal(
            holding_bars,
            stop_loss_price,
            take_profit_price,
            code=code,
            entry_price=entry_price,
            dynamic_trailing=True,
            trailing_policy="profit_floor",
            profit_lock_ratio=ratio,
        )

    return resolver


def _load_population(db) -> tuple[list[Recommendation], dict[str, list[DailyKbar]]]:
    recs = list(
        db.scalars(
            select(Recommendation)
            .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
            .where(*official_sample_conditions())
            .where(Recommendation.final_score >= SCORE_LINE_FLOOR)
            .order_by(Recommendation.trade_date.asc(), Recommendation.code.asc())
        ).all()
    )
    recs = [rec for rec in recs if _in_production_universe(str(rec.code))]
    codes = sorted({str(rec.code) for rec in recs})
    bars_by_code: dict[str, list[DailyKbar]] = defaultdict(list)
    for bar in db.scalars(
        select(DailyKbar)
        .where(DailyKbar.code.in_(codes))
        .order_by(DailyKbar.code.asc(), DailyKbar.trade_date.asc())
    ).all():
        bars_by_code[str(bar.code)].append(bar)
    return recs, bars_by_code


def _replay(
    recs: list[Recommendation],
    bars_by_code: dict[str, list[DailyKbar]],
    *,
    trailing_ratio: float,
) -> list[dict]:
    resolver = _trailing_resolver(trailing_ratio)
    records: list[dict] = []
    for rec in recs:
        bars = bars_by_code.get(str(rec.code)) or []
        sim = _simulate_outcome(
            rec,
            bars,
            [str(bar.trade_date) for bar in bars],
            HORIZON,
            exit_resolver=resolver,
        )
        if not sim or not sim.get("entry_triggered"):
            continue
        if sim.get("outcome_label") not in ("success", "failed"):
            continue
        records.append(
            {
                "recommendation_id": str(rec.recommendation_id),
                "code": str(rec.code),
                "trade_date": str(rec.trade_date),
                "exit_date": str(sim.get("exit_date") or rec.trade_date),
                "net_return": float(sim.get("executed_return") or 0.0) - NET_COST_PCT,
                "exit_reason": str(sim.get("exit_reason") or ""),
            }
        )
    return records


def _portfolio_metrics(records: list[dict]) -> tuple[PerformanceSnapshot, dict]:
    returns = [record["net_return"] for record in records]
    if not returns:
        # Empty population (e.g. a regime slice with no trades).  Return a
        # n=0 placeholder instead of crashing so regime-layered validation can
        # report "this slice has no samples" and move on.  Callers must skip
        # evaluate_challenger when n == 0 (an empty slice is not comparable).
        empty_snapshot = PerformanceSnapshot(0.0, 0.0, 0.0, 0.0, 0.0)
        return empty_snapshot, {"n": 0, "trade_days": 0, "exit_days": 0}
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    profit_factor = sum(wins) / -sum(losses) if losses else float("inf")

    by_exit_date: dict[str, list[float]] = defaultdict(list)
    for record in records:
        by_exit_date[record["exit_date"]].append(record["net_return"])
    daily_returns = [
        sum(by_exit_date[date]) / len(by_exit_date[date])
        for date in sorted(by_exit_date)
    ]
    equity = 1.0
    peak = 1.0
    max_drawdown_pct = 0.0
    for daily_return_pct in daily_returns:
        equity *= 1.0 + daily_return_pct / 100.0
        peak = max(peak, equity)
        max_drawdown_pct = min(max_drawdown_pct, (equity / peak - 1.0) * 100.0)
    cumulative_net_return_pct = (equity - 1.0) * 100.0

    snapshot = PerformanceSnapshot(
        average_net_return_pct=sum(returns) / len(returns),
        cumulative_net_return_pct=cumulative_net_return_pct,
        win_rate_pct=len(wins) / len(returns) * 100.0,
        profit_factor=profit_factor,
        max_drawdown_pct=max_drawdown_pct,
    )
    details = {
        "n": len(returns),
        "trade_days": len({record["trade_date"] for record in records}),
        "exit_days": len(daily_returns),
        **snapshot.__dict__,
    }
    return snapshot, details


def main() -> None:
    with SessionLocal() as db:
        population, bars_by_code = _load_population(db)
        regime_counts: dict[str, int] = defaultdict(int)
        for rec in population:
            regime_counts[_decision_regime(rec) or "MISSING"] += 1

        champion_records = _replay(
            population,
            bars_by_code,
            trailing_ratio=CHAMPION_TRAILING_RATIO,
        )
        champion_snapshot, champion_metrics = _portfolio_metrics(champion_records)

        authoritative_structural_ids = {
            str(rec.recommendation_id)
            for rec in population
            if _decision_regime(rec) == STRUCTURAL_STRONG_HARD_VETO_REGIME
        }
        structural_evidence: dict = {
            "authoritative_decision_regime_counts": dict(sorted(regime_counts.items())),
            "authoritative_structural_strong_n": len(authoritative_structural_ids),
            "can_compare": bool(authoritative_structural_ids),
            "reason": (
                "ready"
                if authoritative_structural_ids
                else "historical production population has no persisted decision-time structural-strong labels; retrospective feature labels are not admissible"
            ),
        }
        effective_population = population
        effective_champion_records = champion_records
        effective_champion_snapshot = champion_snapshot
        if authoritative_structural_ids:
            structural_population = [
                rec
                for rec in population
                if str(rec.recommendation_id) not in authoritative_structural_ids
            ]
            structural_records = _replay(
                structural_population,
                bars_by_code,
                trailing_ratio=CHAMPION_TRAILING_RATIO,
            )
            structural_snapshot, structural_metrics = _portfolio_metrics(structural_records)
            structural_evidence.update({"challenger": structural_metrics})
            if structural_metrics["n"] > 0:
                structural_decision = evaluate_challenger(
                    champion_snapshot,
                    structural_snapshot,
                )
                structural_evidence["decision"] = structural_decision.to_dict()
            else:
                structural_evidence["decision"] = ChallengerDecision(
                    should_publish=False,
                    decision="FALSIFIED",
                    improvements=(),
                    regressions=(),
                    deltas={},
                ).to_dict()
            # The veto is part of current production. The decision indicates
            # whether it should be kept or rolled back; later challengers must
            # always be measured on the production-filtered population.
            effective_population = structural_population
            effective_champion_records = structural_records
            effective_champion_snapshot = structural_snapshot

        trailing_records = _replay(
            effective_population,
            bars_by_code,
            trailing_ratio=CHALLENGER_TRAILING_RATIO,
        )
        _, effective_champion_metrics = _portfolio_metrics(effective_champion_records)
        trailing_snapshot, trailing_metrics = _portfolio_metrics(trailing_records)
        if trailing_metrics["n"] > 0 and effective_champion_metrics["n"] > 0:
            trailing_decision = evaluate_challenger(
                effective_champion_snapshot,
                trailing_snapshot,
            )
        else:
            trailing_decision = ChallengerDecision(
                should_publish=False,
                decision="FALSIFIED",
                improvements=(),
                regressions=(),
                deltas={},
            )

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "production_config": {
            "board_universe": PRODUCTION_BOARD_UNIVERSE,
            "structural_strong_hard_veto_regime": STRUCTURAL_STRONG_HARD_VETO_REGIME,
            "trailing_ratio": CHAMPION_TRAILING_RATIO,
        },
        "production_order": ["20cm-only", "structural-strong candidate", "trailing candidate"],
        "pre_structural_champion": champion_metrics,
        "production_champion": effective_champion_metrics,
        "structural_strong": structural_evidence,
        "trailing_0_90": {
            "sequential_champion": effective_champion_metrics,
            "challenger": trailing_metrics,
            "decision": trailing_decision.to_dict(),
        },
    }
    OUTPUT_DIR.mkdir(exist_ok=True)
    output_path = OUTPUT_DIR / "sequential_champion_replay.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[persist] {output_path}")


if __name__ == "__main__":
    main()
