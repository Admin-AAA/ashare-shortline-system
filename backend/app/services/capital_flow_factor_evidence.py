"""Canonical evidence contract for the LIVE formal capital-flow factor.

One stock on one trade date is one opportunity. Re-running formal on that date must
not multiply the observation. The earliest recommendation is kept so later reruns
cannot cherry-pick a more favourable outcome for the same signal opportunity.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import text

from app.engine.trading_costs import ADDITIONAL_COST_PCT


CAPITAL_FLOW_DISCOVERY_DATE = "2026-07-24"
MIN_OOS_SAMPLE = 30
MIN_OOS_TRADE_DAYS = 10

CAPITAL_FLOW_FIELDS = (
    ("main_net_inflow_yi", "当日主力净流入"),
    ("super_large_net_inflow_yi", "超大单净流入"),
    ("large_net_inflow_yi", "大单净流入"),
    ("cumulative_inflow_3d_yi", "3日累计主力净流入"),
    ("cumulative_inflow_5d_yi", "5日累计主力净流入"),
)

# Keep the de-duplication in SQL so every consumer receives the same population.
# ``recommendation_id LIKE 'rec_%'`` excludes research/backtest projections even
# when a historical row was accidentally labelled formal.
DEDUP_LIVE_FORMAL_CAPITAL_FLOW_SQL = text("""
    WITH ranked AS (
        SELECT r.code, r.trade_date, r.recommendation_id, r.recommended_at,
               o.outcome_label, o.executed_return,
               cf.main_net_inflow_yi, cf.super_large_net_inflow_yi,
               cf.large_net_inflow_yi, cf.cumulative_inflow_3d_yi,
               cf.cumulative_inflow_5d_yi,
               ROW_NUMBER() OVER (
                   PARTITION BY r.code, r.trade_date
                   ORDER BY r.recommended_at ASC, r.recommendation_id ASC
               ) AS sample_rank
        FROM recommendations r
        JOIN recommendation_runs rr ON r.run_id = rr.run_id
        JOIN recommendation_outcomes o ON o.recommendation_id = r.recommendation_id
        JOIN capital_flow cf ON cf.code = r.code AND cf.trade_date = r.trade_date
        WHERE rr.run_mode = 'formal'
          AND r.recommendation_id LIKE 'rec_%'
          AND o.outcome_label IN ('success', 'failed')
          AND cf.main_net_inflow_yi IS NOT NULL
          AND ABS(cf.main_net_inflow_yi) > 0.001
    )
    SELECT code, trade_date, recommendation_id, outcome_label, executed_return,
           main_net_inflow_yi, super_large_net_inflow_yi, large_net_inflow_yi,
           cumulative_inflow_3d_yi, cumulative_inflow_5d_yi
    FROM ranked
    WHERE sample_rank = 1
    ORDER BY trade_date, code
""")


def fetch_dedup_live_formal_capital_flow(db: Any) -> list[dict[str, Any]]:
    """Return one settled LIVE formal observation per ``(code, trade_date)``."""
    return [dict(row._mapping) for row in db.execute(DEDUP_LIVE_FORMAL_CAPITAL_FLOW_SQL).fetchall()]


def _metric_block(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    settled = [row for row in rows if row.get("executed_return") is not None]
    net_returns = [float(row["executed_return"]) - ADDITIONAL_COST_PCT for row in settled]
    n = len(net_returns)
    trade_days = len({str(row["trade_date"]) for row in settled})
    if not n:
        return {
            "n": 0,
            "trade_days": 0,
            "win_rate": None,
            "net_expectancy": None,
            "profit_factor": None,
        }
    profit = sum(value for value in net_returns if value > 0)
    loss = abs(sum(value for value in net_returns if value < 0))
    wins = sum(
        (row.get("outcome_label") == "success")
        if row.get("outcome_label") in {"success", "failed"}
        else float(row["executed_return"]) > 0
        for row in settled
    )
    return {
        "n": n,
        "trade_days": trade_days,
        "win_rate": round(wins * 100 / n, 1),
        "net_expectancy": round(sum(net_returns) / n, 3),
        "profit_factor": round(profit / loss, 2) if loss > 0 else None,
    }


def summarize_factor_groups(
    rows: Sequence[Mapping[str, Any]],
    field: str,
    *,
    after_date: str | None = None,
) -> dict[str, Any]:
    """Summarize positive/non-positive factor groups on one frozen population.

    ``after_date`` is exclusive. A signal discovered after the 2026-07-24 close
    therefore cannot treat recommendations from that same session as OOS.
    """
    eligible = [
        row for row in rows
        if row.get(field) is not None
        and abs(float(row.get(field) or 0)) > 0.001
        and (after_date is None or str(row.get("trade_date")) > after_date)
    ]
    positive = [row for row in eligible if float(row.get(field) or 0) > 0]
    non_positive = [row for row in eligible if float(row.get(field) or 0) <= 0]
    return {
        "sample": _metric_block(eligible),
        "positive": _metric_block(positive),
        "non_positive": _metric_block(non_positive),
    }


def summarize_discovery_oos(
    rows: Sequence[Mapping[str, Any]],
    field: str = "cumulative_inflow_3d_yi",
    *,
    discovery_date: str = CAPITAL_FLOW_DISCOVERY_DATE,
) -> dict[str, Any]:
    """Return the untouched post-discovery window and its promotion gate."""
    groups = summarize_factor_groups(rows, field, after_date=discovery_date)
    signal = groups["positive"]
    sufficient = signal["n"] >= MIN_OOS_SAMPLE and signal["trade_days"] >= MIN_OOS_TRADE_DAYS
    if sufficient:
        verdict = "达到最低观察门槛；仍须与冻结 champion 配对比较，不自动晋级"
    else:
        verdict = (
            f"证据不足：正流入 OOS n={signal['n']}（需≥{MIN_OOS_SAMPLE}）、"
            f"覆盖{signal['trade_days']}个交易日（需≥{MIN_OOS_TRADE_DAYS}），不得晋级"
        )
    return {
        "field": field,
        "discovery_date": discovery_date,
        "window": f"trade_date > {discovery_date}",
        **groups,
        "evidence_sufficient": sufficient,
        "verdict": verdict,
    }
