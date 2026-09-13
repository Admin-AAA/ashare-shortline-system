"""Probe: what population yields the L-086 'LIVE 去重241/已结算205' evidence base?

Read-only. Splits settled rec_* outcomes by evidence_class / evaluation_scope so we can
tell whether the L-086 counterfactual population is canonical LIVE_FORWARD (§2 hard gate)
or a mixed legacy population (L-001).
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import Recommendation, RecommendationOutcome, RecommendationRun  # noqa: E402

SETTLED = ("success", "failed")


def main() -> None:
    db = SessionLocal()
    try:
        rows = db.execute(
            select(
                Recommendation.run_id,
                Recommendation.trade_date,
                Recommendation.code,
                RecommendationOutcome.outcome_label,
                RecommendationRun.run_mode,
                RecommendationRun.status,
                RecommendationRun.evidence_class,
                RecommendationRun.evaluation_scope,
            )
            .join(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
            .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
            .where(
                RecommendationOutcome.outcome_label.in_(SETTLED),
                Recommendation.recommendation_id.like("rec_%"),
            )
        ).all()

        print(f"settled rec_* outcome rows: {len(rows)}")

        def bucket(fn):
            g = defaultdict(lambda: [0, set()])
            for r in rows:
                k = fn(r)
                g[k][0] += 1
                g[k][1].add((r.trade_date, r.code))
            return {k: (v[0], len(v[1])) for k, v in sorted(g.items(), key=lambda kv: -kv[1][0])}

        print("\n--- by (run_mode, status): rows / dedup(date,code) ---")
        for k, v in bucket(lambda r: (r.run_mode, r.status)).items():
            print(f"  {k}: rows={v[0]} dedup={v[1]}")

        print("\n--- by evidence_class: rows / dedup ---")
        for k, v in bucket(lambda r: str(r.evidence_class)).items():
            print(f"  {k}: rows={v[0]} dedup={v[1]}")

        print("\n--- by evaluation_scope: rows / dedup ---")
        for k, v in bucket(lambda r: str(r.evaluation_scope)).items():
            print(f"  {k}: rows={v[0]} dedup={v[1]}")

        print("\n--- by (evidence_class, evaluation_scope): rows / dedup ---")
        for k, v in bucket(lambda r: (str(r.evidence_class), str(r.evaluation_scope))).items():
            print(f"  {k}: rows={v[0]} dedup={v[1]}")

        print("\n--- trade_date span per evidence_class ---")
        spans = defaultdict(list)
        for r in rows:
            spans[str(r.evidence_class)].append(r.trade_date)
        for k, ds in spans.items():
            print(f"  {k}: {min(ds)} .. {max(ds)} ({len(set(ds))} dates)")

        print("\n--- distinct runs per evidence_class ---")
        runs = defaultdict(set)
        for r in rows:
            runs[str(r.evidence_class)].add(r.run_id)
        for k, v in runs.items():
            print(f"  {k}: {len(v)} runs")
    finally:
        db.close()


def timing_probe() -> None:
    """Per stratum: how long AFTER trade_date was the recommendation actually generated?

    Mechanism check for L-001/L-002 caliber pollution: a stratum whose rows were
    generated long after the trade date was produced with hindsight-available data,
    so any factor signature it carries is not admissible release evidence (§2).
    """
    from datetime import date, datetime

    db = SessionLocal()
    try:
        rows = db.execute(
            select(
                Recommendation.run_id,
                Recommendation.trade_date,
                Recommendation.code,
                RecommendationOutcome.outcome_label,
                RecommendationRun.evidence_class,
                RecommendationRun.evaluation_scope,
                RecommendationRun.actual_generated_at,
                RecommendationRun.data_as_of,
            )
            .join(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
            .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
            .where(
                RecommendationOutcome.outcome_label.in_(SETTLED),
                Recommendation.recommendation_id.like("rec_%"),
            )
        ).all()

        def parse_day(td) -> date | None:
            if td is None:
                return None
            if isinstance(td, datetime):
                return td.date()
            if isinstance(td, date):
                return td
            s = str(td)[:10]
            try:
                return datetime.strptime(s, "%Y-%m-%d").date()
            except ValueError:
                return None

        g = defaultdict(list)
        for r in rows:
            td = parse_day(r.trade_date)
            gen = r.actual_generated_at
            if isinstance(gen, datetime):
                gen = gen.date()
            if td is None or not isinstance(gen, date):
                g[str(r.evidence_class)].append(None)
                continue
            g[str(r.evidence_class)].append((gen - td).days)

        print("\n=== generation lag (actual_generated_at - trade_date, days) ===")
        for cls, lags in sorted(g.items(), key=lambda kv: -len(kv[1])):
            known = sorted(v for v in lags if v is not None)
            if not known:
                print(f"  {cls}: n={len(lags)} (no actual_generated_at)")
                continue
            mid = known[len(known) // 2]
            print(
                f"  {cls}: n={len(lags)} known={len(known)} "
                f"min={known[0]} median={mid} max={known[-1]} "
                f"same_or_prev_day={sum(1 for v in known if v <= 0)}/{len(known)}"
            )

        print("\n=== data_as_of vs trade_date (causal freshness) ===")
        fresh = defaultdict(lambda: [0, 0])
        for r in rows:
            td = parse_day(r.trade_date)
            ao = parse_day(r.data_as_of)
            k = str(r.evidence_class)
            fresh[k][1] += 1
            if td is not None and ao is not None and ao <= td:
                fresh[k][0] += 1
        for cls, (ok, tot) in sorted(fresh.items(), key=lambda kv: -kv[1][1]):
            print(f"  {cls}: data_as_of<=trade_date {ok}/{tot} = {100 * ok / max(1, tot):.1f}%")
    finally:
        db.close()


if __name__ == "__main__":
    main()
    timing_probe()
