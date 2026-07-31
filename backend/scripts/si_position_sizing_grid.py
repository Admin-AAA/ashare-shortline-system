"""Self-improve grid_scan:position_sizing — 仓位管理策略网格扫描。

假设：固定均匀仓位（uniform 1.0）或分数加权仓位可改善当前标签驱动的仓位分配，
通过消除系统性的仓位错配（大仓放赔钱票，小仓放赚钱票）提升组合 PF 和累计净收益。

证据路径：20cm clean>=72 formal 已结算 outcome，不改入场/出场/止损/止盈，
仅改变每笔交易的仓位乘数，比较不同策略下的四项指标。

用法：
  backend/.venv/bin/python backend/scripts/si_position_sizing_grid.py
"""

from __future__ import annotations

import json, random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import sys
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.backtest.challenger_decision import (
    PerformanceSnapshot,
    evaluate_challenger,
)

SCORE_LINE_FLOOR = 72.0
NET_COST_PCT = 0.07
OUTPUT_DIR = BACKEND_ROOT.parent / "output"
BOOTSTRAP_N = 10000
BOOTSTRAP_SEED = 20260731


def _is_20cm(code: str) -> bool:
    return str(code or "").startswith(("300", "301", "688", "689"))


def _connect():
    return SessionLocal().connection()


def load_outcomes(conn) -> list[dict]:
    """Load 20cm clean>=72 formal outcomes with scores and current position_size."""
    sql = text("""
        SELECT o.recommendation_id, o.executed_return, o.position_size, o.weighted_return,
               r.trade_date, r.code, r.final_score, o.exit_date
        FROM recommendation_outcomes o
        JOIN recommendations r ON o.recommendation_id = r.recommendation_id
        JOIN recommendation_runs rn ON r.run_id = rn.run_id
        WHERE o.outcome_label IN ('success', 'failed')
          AND o.entry_triggered = 1
          AND r.final_score >= :floor
          AND rn.status = 'completed'
        ORDER BY r.trade_date ASC
    """)
    rows = conn.execute(sql, {"floor": SCORE_LINE_FLOOR}).mappings().all()
    outcomes = []
    for row in rows:
        code = str(row["code"])
        if not _is_20cm(code):
            continue
        outcomes.append({
            "code": code,
            "trade_date": str(row["trade_date"]),
            "executed_return": float(row["executed_return"]),
            "position_size": float(row["position_size"]),
            "weighted_return": float(row["weighted_return"]),
            "score": float(row["final_score"]),
            "exit_date": str(row["exit_date"]) if row["exit_date"] else None,
        })
    return outcomes


def _portfolio_curve_mdd(records: list[dict], sizing_fn: Callable[[dict], float]) -> float:
    """按 exit_date 聚合的真实组合资金曲线最大回撤。

    每天把所有当日退出的仓位（按其收益）等权平均得到日组合收益，
    链式累乘构建资金曲线，再算 peak-to-trough 最大回撤。替代旧的
    min(weighted_rets)（单笔最小收益，非组合曲线，会低估回撤）。
    """
    from collections import defaultdict
    by_exit: dict[str, list[float]] = defaultdict(list)
    for rec in records:
        exit_date = rec.get("exit_date")
        if not exit_date:
            continue
        pos = sizing_fn(rec)
        net_ret = rec["executed_return"] - NET_COST_PCT
        by_exit[exit_date].append(net_ret * pos)

    if not by_exit:
        return 0.0

    running = 1.0
    peak = 1.0
    max_dd = 0.0
    for exit_date in sorted(by_exit):
        daily_ret = sum(by_exit[exit_date]) / len(by_exit[exit_date])
        running *= (1 + daily_ret / 100)
        peak = max(peak, running)
        max_dd = min(max_dd, (running - peak) / peak * 100)
    return round(max_dd, 4)


def compute_metrics(records: list[dict], sizing_fn: Callable[[dict], float]) -> dict:
    """Compute portfolio-level metrics with the given sizing function.

    sizing_fn(record) -> position_size in (0, 1.0]
    """
    weighted_rets = []
    raw_rets = []
    for rec in records:
        pos = sizing_fn(rec)
        net_ret = rec["executed_return"] - NET_COST_PCT
        raw_rets.append(net_ret)
        weighted_rets.append(net_ret * pos)

    n = len(weighted_rets)
    if n == 0:
        return {"n": 0}

    avg_net = sum(weighted_rets) / n
    cum_net = sum(weighted_rets)
    wins_weighted = [x for x in weighted_rets if x > 0]
    losses_weighted = [x for x in weighted_rets if x < 0]
    wins_raw = sum(1 for x in raw_rets if x > 0)
    win_rate = wins_raw / n * 100

    total_gain = sum(wins_weighted) if wins_weighted else 0.0
    total_loss = abs(sum(losses_weighted)) if losses_weighted else 0.0
    profit_factor = round(total_gain / total_loss, 4) if total_loss > 0 else float("inf")
    max_drawdown = _portfolio_curve_mdd(records, sizing_fn)

    return {
        "n": n,
        "average_net_return_pct": round(avg_net, 4),
        "cumulative_net_return_pct": round(cum_net, 2),
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_drawdown,
        "raw_avg_net_return": round(sum(raw_rets) / n, 4),
        "raw_cumulative_net": round(sum(raw_rets), 2),
    }


def paired_bootstrap(records: list[dict],
                     fn_champ: Callable[[dict], float],
                     fn_chall: Callable[[dict], float]) -> dict:
    """Paired date-clustered bootstrap for avg_net difference.

    Resamples trade_dates (clusters) with replacement to account for
    within-day correlation of trades. Computes the mean per-trade
    diff for each bootstrap resample.
    """
    # Group per-trade diffs by date
    net_rets = {r["code"] + "|" + r["trade_date"]: r["executed_return"] - NET_COST_PCT for r in records}
    by_date = defaultdict(list)
    for rec in records:
        d = rec["trade_date"]
        net = rec["executed_return"] - NET_COST_PCT
        by_date[d].append({
            "net": net,
            "pos_champ": fn_champ(rec),
            "pos_chall": fn_chall(rec),
        })

    dates = sorted(by_date)
    n_dates = len(dates)
    if n_dates < 3:
        return {"paired_n": len(records), "n_dates": n_dates, "mean_diff": 0.0,
                "ci95": [0.0, 0.0], "ci_excludes_zero": False}

    # Per-date mean weighted returns
    date_avg_champ = []
    date_avg_chall = []
    for d in dates:
        trades = by_date[d]
        date_avg_champ.append(sum(t["net"] * t["pos_champ"] for t in trades) / len(trades))
        date_avg_chall.append(sum(t["net"] * t["pos_chall"] for t in trades) / len(trades))

    mean_diff = sum(date_avg_chall) / n_dates - sum(date_avg_champ) / n_dates

    # Bootstrap: resample dates with replacement
    rng = random.Random(BOOTSTRAP_SEED)
    boot_means = []
    for _ in range(BOOTSTRAP_N):
        # Resample n_dates dates with replacement
        idx_sample = [rng.randrange(n_dates) for _ in range(n_dates)]
        boot_champ = sum(date_avg_champ[i] for i in idx_sample) / n_dates
        boot_chall = sum(date_avg_chall[i] for i in idx_sample) / n_dates
        boot_means.append(boot_chall - boot_champ)

    boot_means.sort()
    lo = boot_means[int(0.025 * BOOTSTRAP_N)]
    hi = boot_means[int(0.975 * BOOTSTRAP_N)]
    return {
        "paired_n": len(records),
        "n_dates": n_dates,
        "mean_diff": round(mean_diff, 4),
        "ci95": [round(lo, 4), round(hi, 4)],
        "ci_excludes_zero": bool(lo > 0 or hi < 0),
    }


# === Sizing Policies ===

def current_sizing(rec: dict) -> float:
    """生产 champion (3c129ba): score_bucket_85_halve — final_score>=85 半仓。

    注意：DB 的 position_size 是历史 cap-based 结算值，不代表当前生产。
    生产 _resolve_position_size 用 final_score>=85 → 0.5（_simulate_outcome 注入
    rec.final_score）。用此函数重放 70 笔得到 avg=1.406%/PF=1.895/MDD=-10.03%，
    与 champion 状态 metrics 完全一致。
    """
    return 0.5 if rec["score"] >= 85.0 else 1.0


def uniform_1_0(rec: dict) -> float:
    """Uniform 1.0: all trades equal weight."""
    return 1.0


def score_linear_03(rec: dict) -> float:
    """Score-linear: pos = max(0.3, score/100)."""
    return round(max(0.3, rec["score"] / 100.0), 2)


def score_linear_05(rec: dict) -> float:
    """Score-linear: pos = max(0.5, score/100)."""
    return round(max(0.5, rec["score"] / 100.0), 2)


def score_bucket_3tier(rec: dict) -> float:
    """3-tier score buckets: <80 -> 0.5, 80-85 -> 1.0, >=85 -> 0.7."""
    s = rec["score"]
    if s < 80:
        return 0.5
    elif s < 85:
        return 1.0
    else:
        return 0.7


def score_bucket_90_quarter(rec: dict) -> float:
    """90+ quarter weight: score<90 full, >=90 0.25."""
    return 0.25 if rec["score"] >= 90.0 else 1.0


POLICIES = {
    "champion_score_bucket_85_halve": current_sizing,
    "uniform_1.0": uniform_1_0,
    "score_linear_0.3-1.0": score_linear_03,
    "score_linear_0.5-1.0": score_linear_05,
    "score_bucket_3tier": score_bucket_3tier,
    "score_bucket_90_quarter": score_bucket_90_quarter,
}


def main():
    conn = _connect()
    try:
        outcomes = load_outcomes(conn)
        print(f"[data] 20cm clean>={SCORE_LINE_FLOOR} formal outcomes: {len(outcomes)}")

        # Date range
        dates = sorted(set(o["trade_date"] for o in outcomes))
        print(f"[data] trade dates: {len(dates)} ({dates[0]} -> {dates[-1]})")

        # Score distribution
        scores = [o["score"] for o in outcomes]
        print(f"[data] score: mean={sum(scores)/len(scores):.2f} "
              f"min={min(scores):.1f} max={max(scores):.1f}")

        # Champion metrics
        champ_metrics = compute_metrics(outcomes, current_sizing)
        print(f"\n=== CHAMPION (current label-based) ===")
        print(f"  n={champ_metrics['n']} avg_net={champ_metrics['average_net_return_pct']:+.4f}% "
              f"cum_net={champ_metrics['cumulative_net_return_pct']:+.2f}% "
              f"wr={champ_metrics['win_rate_pct']:.1f}% "
              f"pf={champ_metrics['profit_factor']:.4f} "
              f"max_dd={champ_metrics['max_drawdown_pct']:+.2f}%")
        print(f"  raw: avg_net={champ_metrics['raw_avg_net_return']:+.4f}% "
              f"raw_cum={champ_metrics['raw_cumulative_net']:+.2f}%")

        champ_snapshot = PerformanceSnapshot(
            average_net_return_pct=champ_metrics["average_net_return_pct"],
            cumulative_net_return_pct=champ_metrics["cumulative_net_return_pct"],
            win_rate_pct=champ_metrics["win_rate_pct"],
            profit_factor=champ_metrics["profit_factor"],
            max_drawdown_pct=champ_metrics["max_drawdown_pct"],
        )

        print(f"\n=== CHALLENGERS ===")
        results = []
        for name, sizing_fn in POLICIES.items():
            if name == "champion_score_bucket_85_halve":
                continue  # This is the champion

            m = compute_metrics(outcomes, sizing_fn)
            boot = paired_bootstrap(outcomes, current_sizing, sizing_fn)

            chall_snapshot = PerformanceSnapshot(
                average_net_return_pct=m["average_net_return_pct"],
                cumulative_net_return_pct=m["cumulative_net_return_pct"],
                win_rate_pct=m["win_rate_pct"],
                profit_factor=m["profit_factor"],
                max_drawdown_pct=m["max_drawdown_pct"],
            )

            decision = evaluate_challenger(champ_snapshot, chall_snapshot)

            print(f"\n  --- {name} ---")
            print(f"  avg_net={m['average_net_return_pct']:+.4f}% ({m['average_net_return_pct']-champ_metrics['average_net_return_pct']:+.4f}pp)")
            print(f"  cum_net={m['cumulative_net_return_pct']:+.2f}% ({m['cumulative_net_return_pct']-champ_metrics['cumulative_net_return_pct']:+.2f}pp)")
            print(f"  wr={m['win_rate_pct']:.1f}% ({m['win_rate_pct']-champ_metrics['win_rate_pct']:+.1f}pp)")
            print(f"  pf={m['profit_factor']:.4f} ({m['profit_factor']-champ_metrics['profit_factor']:+.4f})")
            print(f"  max_dd={m['max_drawdown_pct']:+.2f}% ({m['max_drawdown_pct']-champ_metrics['max_drawdown_pct']:+.2f}pp)")
            print(f"  decision: {decision.decision}")
            print(f"  improvements: {decision.improvements}")
            print(f"  regressions: {decision.regressions}")
            print(f"  deltas: {decision.deltas}")
            print(f"  bootstrap: {boot}")

            # Per-score bucket breakdown for this policy
            bins = [(72, 80), (80, 85), (85, 90), (90, 200)]
            print(f"  bucket breakdown:")
            for lo, hi in bins:
                bucket = [o for o in outcomes if lo <= o["score"] < hi]
                if not bucket:
                    continue
                bm = compute_metrics(bucket, sizing_fn)
                print(f"    score [{lo},{hi}): n={bm['n']:3d} avg_net={bm['average_net_return_pct']:+.4f}%")

            results.append({
                "name": name,
                "metrics": m,
                "decision": decision.to_dict(),
                "paired_bootstrap": boot,
            })

        # Save results
        out = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "label": "grid_scan:position_sizing",
            "data": {
                "n_outcomes": len(outcomes),
                "n_dates": len(dates),
                "date_range": [dates[0], dates[-1]],
                "score_floor": SCORE_LINE_FLOOR,
                "board": "20cm",
                "net_cost_pct": NET_COST_PCT,
                "bootstrap_n": BOOTSTRAP_N,
                "bootstrap_seed": BOOTSTRAP_SEED,
            },
            "champion": champ_metrics,
            "challengers": results,
        }
        out_path = OUTPUT_DIR / "position_sizing_grid_20260731.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        print(f"\n[written] {out_path}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
