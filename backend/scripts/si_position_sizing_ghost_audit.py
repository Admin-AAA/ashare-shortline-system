"""Self-improve audit: position-sizing "ghost champion" measurement defect.

Evidence (2026-07-31, LIVE formal 20cm clean>=72):
- Champion commit 3c129ba ("score_bucket_85_halve") published metrics
  avg=1.406% were computed with PURE score-bucket weights (score>=85 -> 0.5),
  bypassing production `_resolve_position_size`.
- Production `_resolve_position_size` applies `market_discipline.max_position_pct`
  as a hard cap that RETURNS FIRST, so the score-bucket branch is unreachable
  whenever a cap exists (65/70 records).
- Pre-champion (17dba80) and post-champion (3c129ba) resolvers output identical
  weights on the executed population -> 0/70 records differ -> the published
  champion is a no-op on real execution.
- Real executable champion metric: avg = 0.234% (cap-weighted), NOT 1.406%.

This script reproduces the gap across four weight calibers so the finding is
re-runnable:
  A. equal-weight
  B. pure score-bucket (what 3c129ba's validation assumed)
  C. production resolver cap-first (current code, score-bucket unreachable)
  D. fixed min(cap, score_bucket) (intended semantics)

Usage:
  backend/.venv/bin/python backend/scripts/si_position_sizing_ghost_audit.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import sqlalchemy as sa

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.outcome_simulation import (  # noqa: E402
    _resolve_position_size,
    _as_float,
)

DATABASE_URL = "mysql+pymysql://aowang:1279990918@127.0.0.1:3306/ashare_workbench"
SCORE_LINE_FLOOR = 72.0
NET_COST_PCT = 0.07
OUTPUT_DIR = BACKEND_ROOT.parent / "output"


def _is_20cm(code: str) -> bool:
    return str(code or "").startswith(("300", "301", "688", "689"))


def load_outcomes(conn) -> list[dict]:
    """Loose population (what 3c129ba's n=70 validation used): formal/exp/bt
    all scopes, no official_sample_conditions filter."""
    sql = sa.text(
        """
        SELECT o.recommendation_id, o.executed_return, o.position_size,
               r.code, r.trade_date, r.final_score, r.feature_snapshot_json
        FROM recommendation_outcomes o
        JOIN recommendations r ON o.recommendation_id = r.recommendation_id
        JOIN recommendation_runs rn ON r.run_id = rn.run_id
        WHERE o.outcome_label IN ('success', 'failed')
          AND o.entry_triggered = 1
          AND r.final_score >= :floor
          AND rn.status = 'completed'
        ORDER BY r.trade_date ASC
        """
    )
    rows = conn.execute(sql, {"floor": SCORE_LINE_FLOOR}).mappings().all()
    out = []
    for row in rows:
        code = str(row["code"])
        if not _is_20cm(code):
            continue
        fs = json.loads(row["feature_snapshot_json"] or "{}")
        if "final_score" not in fs:
            fs["final_score"] = _as_float(row["final_score"])
        out.append(
            {
                "code": code,
                "trade_date": str(row["trade_date"]),
                "executed_return": float(row["executed_return"]),
                "db_position_size": float(row["position_size"]),
                "score": float(row["final_score"]),
                "feature_snapshot": fs,
            }
        )
    return out


def compute(records: list[dict], weight_fn) -> dict:
    """Per-trade metrics for a weight policy. Equal-weight per-trade avg is the
    primary comparison; equity-curve MDD uses exit-free date = trade_date."""
    net_rets = [r["executed_return"] - NET_COST_PCT for r in records]
    weighted = [(r["executed_return"] - NET_COST_PCT) * weight_fn(r) for r in records]
    n = len(weighted)
    wins_w = [x for x in weighted if x > 0]
    losses_w = [x for x in weighted if x < 0]
    total_gain = sum(wins_w)
    total_loss = abs(sum(losses_w))
    pf = total_gain / total_loss if total_loss > 0 else float("inf")
    raw_wr = sum(1 for x in net_rets if x > 0) / n * 100

    # Equity curve MDD (trade_date aligned, equal-weight per day)
    by_date: dict[str, list[float]] = defaultdict(list)
    for r, w in zip(records, weighted):
        by_date[r["trade_date"]].append(w)
    equity, peak, mdd = 1.0, 1.0, 0.0
    for d in sorted(by_date):
        day_ret = sum(by_date[d]) / len(by_date[d])
        equity *= 1.0 + day_ret / 100.0
        peak = max(peak, equity)
        mdd = min(mdd, (equity / peak - 1.0) * 100.0)

    return {
        "n": n,
        "avg_net": round(sum(weighted) / n, 4),
        "cum_net": round((equity - 1.0) * 100.0, 2),
        "win_rate": round(raw_wr, 2),
        "pf": round(pf, 4),
        "max_drawdown": round(mdd, 2),
        "raw_avg": round(sum(net_rets) / n, 4),
    }


def main() -> None:
    engine = sa.create_engine(DATABASE_URL)
    with engine.connect() as conn:
        records = load_outcomes(conn)

    def weight_equal(r):
        return 1.0

    def weight_pure_score_bucket(r):
        return 0.5 if r["score"] >= 85.0 else 1.0

    def weight_prod_resolver(r):
        return _resolve_position_size(r["feature_snapshot"])

    def weight_fixed_min(r):
        size = 0.5 if r["score"] >= 85.0 else 1.0
        fs = r["feature_snapshot"]
        discipline = fs.get("market_discipline")
        if isinstance(discipline, dict):
            cap = _as_float(discipline.get("max_position_pct"))
        else:
            cap = _as_float(fs.get("max_position_pct"))
        if cap > 0:
            return round(min(cap / 100.0, size), 4)
        return size

    policies = {
        "A_equal_weight": weight_equal,
        "B_pure_score_bucket": weight_pure_score_bucket,
        "C_prod_resolver_cap_first": weight_prod_resolver,
        "D_fixed_min_cap_score": weight_fixed_min,
    }

    print(f"[data] n={len(records)} 20cm clean>={SCORE_LINE_FLOOR} (loose scope, n=70 口径)")
    print()
    results = {}
    for name, fn in policies.items():
        m = compute(records, fn)
        results[name] = m
        print(f"  {name:<28} avg={m['avg_net']:+.4f}% cum={m['cum_net']:+.2f}% "
              f"wr={m['win_rate']:.1f}% pf={m['pf']:.4f} mdd={m['max_drawdown']:+.2f}%")

    # Prove ghost champion: C vs A on same population
    c_prod = results["C_prod_resolver_cap_first"]
    a_eq = results["A_equal_weight"]
    b_pure = results["B_pure_score_bucket"]
    print()
    print("=== 幽灵 champion 证据 ===")
    print(f"  028 验证口径 (B pure score-bucket) avg={b_pure['avg_net']:+.4f}%  ← 发布 metric 依据")
    print(f"  生产真实执行 (C prod resolver)   avg={c_prod['avg_net']:+.4f}%  ← 实际可执行口径")
    print(f"  等权 (A)                        avg={a_eq['avg_net']:+.4f}%")
    print(f"  差值 B-C = {b_pure['avg_net']-c_prod['avg_net']:+.4f}pp —— 验证假设与执行脱节 {abs(b_pure['avg_net']-c_prod['avg_net'])/max(abs(c_prod['avg_net']),0.01):.0f}x")
    print(f"  028 声称 avg=1.406%, 真实可执行 avg={c_prod['avg_net']:+.4f}% —— 差 {abs(1.406-c_prod['avg_net']):.3f}pp")

    out = {
        "label": "position_sizing_ghost_champion_audit",
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "n": len(records),
        "score_floor": SCORE_LINE_FLOOR,
        "net_cost_pct": NET_COST_PCT,
        "policies": results,
        "conclusion": (
            "C (prod resolver) == 17dba80 输出：cap-first 分支先返回，score-bucket 分支不可达。"
            "028 验证的 1.406% 是 B (pure score-bucket) 口径，非生产执行口径。"
            "真实可执行 champion avg=0.234% (C)。"
        ),
    }
    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / "position_sizing_ghost_audit.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n[written] {out_path}")


if __name__ == "__main__":
    main()
