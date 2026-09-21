from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, case, func, or_, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal
from app.engine.aggregator import aggregate
from app.engine.board_rules import limit_pct_for_stock
from app.engine.strategy_runner import run_strategy
from app.models.tables import DailyKbar, DragonTiger, StockMetadataHistory
from app.services.feature_builder import market_regime_score
from scripts.research_entry_mode_three_window import _decision, _metrics, _simulate_row


def load_evaluator():
    path = ROOT / "skills" / "dragon-tiger-reaccumulation" / "evaluator.py"
    spec = importlib.util.spec_from_file_location("dragon_tiger_reaccumulation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_history(start="2024-12-01", end="2026-09-18"):
    with SessionLocal() as db:
        events = list(db.scalars(select(DragonTiger).where(
            DragonTiger.trade_date >= start, DragonTiger.trade_date <= end,
        ).order_by(DragonTiger.code, DragonTiger.trade_date)))
        codes = sorted({event.code for event in events})
        calendar = list(db.scalars(select(DailyKbar.trade_date).where(
            DailyKbar.trade_date >= start, DailyKbar.trade_date <= end,
        ).distinct().order_by(DailyKbar.trade_date)))
        columns = [getattr(DailyKbar, key) for key in (
            "code", "trade_date", "open", "high", "low", "close", "volume",
            "amount", "pct_chg", "turnover_rate",
        )]
        bars = list(db.execute(select(*columns).where(
            DailyKbar.code.in_(codes), DailyKbar.trade_date >= start,
            DailyKbar.trade_date <= end,
        ).order_by(DailyKbar.code, DailyKbar.trade_date)).mappings())
        metadata = list(db.scalars(select(StockMetadataHistory).where(
            StockMetadataHistory.code.in_(codes), StockMetadataHistory.effective_from <= end,
            (StockMetadataHistory.effective_to.is_(None)) | (StockMetadataHistory.effective_to >= start),
        )))
    bars_by_code, events_by_code, metadata_by_code = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in bars:
        bars_by_code[row["code"]].append(SimpleNamespace(**row))
    for row in events:
        events_by_code[row.code].append(row)
    for row in metadata:
        metadata_by_code[row.code].append(row)
    return calendar, bars_by_code, events_by_code, metadata_by_code


def metadata_on(rows, day):
    visible = [row for row in rows if row.effective_from <= day and (row.effective_to is None or row.effective_to >= day)]
    if len(visible) > 1:
        raise ValueError(f"overlapping historical metadata: {visible[0].code}:{day}")
    if not visible:
        return {}
    row = visible[0]
    return {"name": row.name, "market": row.market, "is_st": row.is_st, "list_date": row.list_date}


def load_market_snapshots(start, end):
    limit = case(
        (StockMetadataHistory.is_st.is_(True), 5.0),
        (StockMetadataHistory.market.in_(["BJ", "北交所"]), 30.0),
        (or_(DailyKbar.code.like("300%"), DailyKbar.code.like("301%"), DailyKbar.code.like("688%"), DailyKbar.code.like("689%")), 20.0),
        else_=10.0,
    )
    query = select(
        DailyKbar.trade_date,
        func.count().label("sample_count"),
        func.sum(case((DailyKbar.pct_chg > 0, 1), else_=0)).label("up_count"),
        func.sum(case((DailyKbar.pct_chg < 0, 1), else_=0)).label("down_count"),
        func.sum(case((DailyKbar.pct_chg >= limit - 0.2, 1), else_=0)).label("limit_up_count"),
        func.sum(case((DailyKbar.pct_chg <= -limit + 0.2, 1), else_=0)).label("limit_down_count"),
    ).join(StockMetadataHistory, and_(
        StockMetadataHistory.code == DailyKbar.code,
        StockMetadataHistory.effective_from <= DailyKbar.trade_date,
        or_(StockMetadataHistory.effective_to.is_(None), StockMetadataHistory.effective_to >= DailyKbar.trade_date),
    )).where(DailyKbar.trade_date >= start, DailyKbar.trade_date <= end).group_by(DailyKbar.trade_date)
    with SessionLocal() as db:
        return {row["trade_date"]: {key: str(value) if key == "trade_date" else int(value) for key, value in row.items()} for row in db.execute(query).mappings()}


def paired_ci(baseline, proposed, days):
    def daily(records):
        values = defaultdict(list)
        for row in records:
            if row["settled"]:
                values[row["signal_date"]].append(row["net_return_pct"])
        return {day: float(np.mean(items)) for day, items in values.items()}
    before, after = daily(baseline), daily(proposed)
    delta = np.array([after.get(day, 0.0) - before.get(day, 0.0) for day in days])
    rng = np.random.default_rng(20260921)
    samples = delta[rng.integers(0, len(delta), size=(4000, len(delta)))].mean(axis=1)
    return {"mean_delta_pct": float(delta.mean()), "ci95_pct": np.quantile(samples, [0.025, 0.975]).tolist(), "trade_days": len(days)}


def metrics(records, days):
    if not any(row["settled"] for row in records):
        return {"n": 0, "status": "insufficient_sample"}
    result = _metrics(records)
    result["active_day_coverage_pct"] = 100 * len({row["signal_date"] for row in records if row["settled"]}) / len(days)
    labelled = [row for row in records if row.get("surge_label") is not None]
    result["surge_label_n"] = len(labelled)
    result["surge_rate_pct"] = 100 * sum(row["surge_label"] for row in labelled) / len(labelled) if labelled else None
    return result


def surge_label(row, bars, calendar):
    day = row["signal_date"]
    future_dates = [value for value in calendar if value > day][:5]
    future = [bar for bar in bars if bar.trade_date in future_dates]
    if len(future_dates) < 5 or [bar.trade_date for bar in future] != future_dates:
        return None
    if any(float(bar.volume) <= 0 for bar in future):
        return None
    threshold = limit_pct_for_stock(row["context"])
    return max(float(bar.close) for bar in future) / float(future[0].open) - 1 >= threshold / 100


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--output-count", type=int, required=True, choices=[6])
    args = parser.parse_args()
    evaluator = load_evaluator()
    baseline_skill = replace(evaluator.SKILL, contract={**evaluator.SKILL.contract, "require_setup": False})
    calendar, bars_by_code, events_by_code, metadata_by_code = load_history()
    market_snapshots = load_market_snapshots("2025-01-15", "2026-09-18")
    windows = {"W1": ("2025-01-15", "2026-01-16"), "W2": ("2026-01-17", "2026-07-30"), "W3": ("2026-08-01", "2026-09-18")}
    output = {
        "strategy_id": evaluator.SKILL.skill_id,
        "status": "RESEARCH_ONLY",
        "caliber": {
            "evidence": "strategy_signal_backtest",
            "production_end_to_end": False,
            "score": "aggregate final_score from single strategy, historical market breadth and observed event/OHLCV features",
            "missing_auxiliary": ["news", "announcements", "auction", "seat_identity", "minute_execution"],
            "output_count": args.output_count,
            "entry_mode": evaluator.SKILL.contract["default_entry_mode"],
            "lookback": "one_calendar_month",
            "event_availability": "strictly_before_signal_date",
            "execution": "production _simulate_outcome; five trading days; current fees",
            "position": "production _resolve_position_size",
            "coverage": "settled trades / selected signals",
            "capital_utilization": "mean allocated position per selected signal; no portfolio overlap accounting",
            "drawdown": "additive mean return by signal day; not account equity drawdown",
            "bootstrap": "paired signal-day mean net return difference; all window dates; absent signals zero; pending excluded",
            "baseline": "same historical event pool, quality score ranking, without the consolidation setup requirement",
            "surge_label": "max close over next five market trading days / next open - 1 >= board price limit; descriptive, not executable profit",
            "deduplication": "first selected signal per code and latest listing event within each window",
            "reproduce": ["backend/.venv/bin/python backend/scripts/research_dragon_tiger_reaccumulation.py --output-count 6 --out output/dragon-tiger-reaccumulation-review-2026-09-21.json"],
        },
        "windows": {},
    }
    for window, (start, end) in windows.items():
        days = [day for day in calendar if start <= day <= end]
        records = {"baseline": [], "strategy": []}
        seen = {"baseline": set(), "strategy": set()}
        diagnostics = Counter()
        features = []
        for day in days:
            month_start = (date.fromisoformat(day) - relativedelta(months=1)).isoformat()
            pools = {"baseline": [], "strategy": []}
            for code, events in events_by_code.items():
                if not any(month_start <= event.trade_date < day for event in events):
                    continue
                metadata = metadata_on(metadata_by_code[code], day)
                context = evaluator.build_context(code, day, bars_by_code[code], events, metadata, calendar)
                if not context.get("dtr_data_ready") or context["amount_yi"] < 2 or context["is_limit_up"]:
                    continue
                diagnostics["eligible_stock_days"] += 1
                signal = run_strategy(evaluator.SKILL, context, [])
                ranking_signal = run_strategy(baseline_skill, context, [])
                snapshot = market_snapshots[day]
                stock = {**context, "market_sample_count": snapshot["sample_count"], "data_quality": {"missing_fields": ["news", "announcements", "auction"]}}
                card = aggregate(stock, [ranking_signal], market_regime_score(snapshot), 0.0, market_snapshot=snapshot)
                card["bucket"] = evaluator.SKILL.skill_id
                card["score_line_meta"] = {"decision_regime": card["market_regime"]}
                row = {"signal_date": day, "code": code, "card": card, "context": context}
                pools["baseline"].append(row)
                if not signal["hard_veto"] and signal["setup_match"]:
                    pools["strategy"].append(row)
                    diagnostics["setup_stock_days"] += 1
            for arm, pool in pools.items():
                available = [row for row in pool if (row["code"], row["context"]["dtr_event_date"]) not in seen[arm]]
                selected = sorted(available, key=lambda row: (-row["card"]["final_score"], row["code"]))[:args.output_count]
                for row in selected:
                    seen[arm].add((row["code"], row["context"]["dtr_event_date"]))
                    record = _simulate_row(row, bars_by_code[row["code"]], challenger=False, challenger_best_strategies=frozenset())
                    record["surge_label"] = surge_label(row, bars_by_code[row["code"]], calendar)
                    records[arm].append(record)
                    if arm == "strategy":
                        features.append({**record, "features": row["context"]})
            if days.index(day) % 40 == 0:
                print(window, day, {key: len(value) for key, value in records.items()}, flush=True)
        baseline, strategy = (metrics(records[arm], days) for arm in ("baseline", "strategy"))
        sufficient = strategy["n"] >= 20
        hard_target = sufficient and strategy["win_rate_pct"] >= 70 and strategy["profit_factor"] >= 1.2 and strategy["cumulative_net_return_pct"] > 0
        decision = _decision(baseline, strategy) if baseline["n"] and strategy["n"] else {"decision": "FALSIFIED", "reason": "insufficient_sample"}
        output["windows"][window] = {
            "date_range": [start, end], "trade_days": len(days), "diagnostics": dict(diagnostics),
            "baseline": baseline, "strategy": strategy,
            "paired_daily_bootstrap": paired_ci(records["baseline"], records["strategy"], days),
            "sample_sufficient": sufficient, "absolute_target_pass": hard_target,
            "challenger_decision": decision, "signals": features,
            "source_coverage": {
                "dragon_tiger_first_date": min(event.trade_date for events in events_by_code.values() for event in events),
                "dragon_tiger_last_date": max(event.trade_date for events in events_by_code.values() for event in events),
                "calendar_month_warmup_complete_at_window_start": (date.fromisoformat(start) - relativedelta(months=1)).isoformat() >= min(event.trade_date for events in events_by_code.values() for event in events),
            },
        }
        print(window, json.dumps(strategy, ensure_ascii=False), flush=True)
    output["status"] = "FALSIFIED" if any(not item["absolute_target_pass"] or item["challenger_decision"]["decision"] != "PRODUCTION" for item in output["windows"].values()) else "REQUIRES_PRODUCTION_REPLAY"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
