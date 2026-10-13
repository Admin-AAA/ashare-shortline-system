"""PATH-A score_line grid scan v2 — 组合级 abstain-as-cash + 分年 + 配对 bootstrap。

关键修复：
1. Champion vs Challenger 必须按相同交易日集合比较，challenger 弃权日把
   champion 在该日的交易也视为 0 收益（L-015 组合级测量）
2. 分年验证（L-019 跨窗口正期望）
"""
from __future__ import annotations

import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv
load_dotenv(BACKEND_ROOT / ".env")

from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.tables import DailyKbar, Recommendation
from app.services.backtest.challenger_decision import (
    PerformanceSnapshot,
    evaluate_challenger,
)

# 与当前生产 champion 保持一致（见 self-improve-state.json canonical_production.rules；
# trailing_profit_lock_ratio 已由 582a71f 从 0.30 提到 0.50）。
CHAMPION_STOP = -4.5
CHAMPION_TP = 6.0
CHAMPION_TRAILING = 0.50
NET_COST = 0.07
HORIZON = 5
EXCLUDED = {"daily-stock-analysis-skill"}


def _20cm(c):
    return str(c).startswith(("300", "301", "688", "689"))


def _loads(r):
    try:
        v = json.loads(r or "{}")
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _is_prod(rec):
    code = str(rec.code or "")
    if not _20cm(code):
        return False
    sigs = json.loads(rec.skill_breakdown_json or "[]")
    if not isinstance(sigs, list) or not sigs:
        return False
    sids = {str(s.get("strategy_id") or s.get("skill_id") or "") for s in sigs if isinstance(s, dict)}
    if sids == EXCLUDED:
        return False
    feat = _loads(rec.feature_snapshot_json)
    meta = feat.get("score_line_meta") or {}
    dr = meta.get("decision_regime") or feat.get("market_regime") or ""
    if str(dr) == "结构性强势":
        return False
    return True


def _filter_by_score_line(recs, score_line):
    passed = []
    for r in recs:
        fs = float(r.final_score or 0)
        if fs <= 0:
            continue
        feat = _loads(r.feature_snapshot_json)
        meta = feat.get("score_line_meta") or {}
        old_base = float(meta.get("base_score_line", 50))
        old_eff = float(meta.get("effective_score_line", 50))
        penalties = old_eff - old_base
        if fs >= score_line + penalties:
            passed.append(r)
    return passed


def _sim_exits(recs, stop=-4.5, tp=6.0, trailing=0.30):
    from app.services.outcome_simulation import (
        DEFAULT_STOP_LOSS_PCT, DEFAULT_TAKE_PROFIT_PCT,
        MAX_STOP_LOSS_PCT, TRAILING_STOP_PROFIT_LOCK_RATIO,
        _simulate_outcome,
    )
    from app.services.outcome_service import normalized_outcome_label
    from app.services.outcome_simulation import CAUSAL_STRATEGY_ENTRY
    import app.services.outcome_simulation as sim_mod

    o_stop = DEFAULT_STOP_LOSS_PCT
    o_tp = DEFAULT_TAKE_PROFIT_PCT
    o_ratio = TRAILING_STOP_PROFIT_LOCK_RATIO
    o_max = MAX_STOP_LOSS_PCT
    sim_mod.DEFAULT_STOP_LOSS_PCT = stop
    sim_mod.DEFAULT_TAKE_PROFIT_PCT = tp
    sim_mod.TRAILING_STOP_PROFIT_LOCK_RATIO = trailing
    sim_mod.MAX_STOP_LOSS_PCT = stop - 0.2
    try:
        codes = sorted({str(r.code) for r in recs if r.code})
        db = SessionLocal()
        try:
            bars = list(db.scalars(
                select(DailyKbar)
                .where(DailyKbar.code.in_(codes))
                .order_by(DailyKbar.code.asc(), DailyKbar.trade_date.asc())
            ).all())
        finally:
            db.close()
        bc: dict[str, list] = defaultdict(list)
        dc: dict[str, list[str]] = defaultdict(list)
        for b in bars:
            c = str(b.code)
            bc[c].append(b)
            dc[c].append(str(b.trade_date))

        records = []
        for r in recs:
            code = str(r.code or "")
            cb = bc.get(code, [])
            cd = dc.get(code, [])
            if not cb or not cd:
                continue
            sim = _simulate_outcome(r, cb, cd, HORIZON, entry_policy=CAUSAL_STRATEGY_ENTRY)
            if not sim or not sim.get("entry_triggered"):
                continue
            label = normalized_outcome_label(
                type("Fx", (), {
                    "outcome_label": sim.get("outcome_label", ""),
                    "entry_triggered": sim.get("entry_triggered", False),
                })()
            )
            if label not in ("success", "failed"):
                continue
            records.append({
                "code": code,
                "trade_date": str(r.trade_date),
                "exit_date": str(sim.get("exit_date") or ""),
                "net_return": float(sim.get("executed_return") or 0) - NET_COST,
                "exit_reason": str(sim.get("exit_reason") or ""),
            })
        return records
    finally:
        sim_mod.DEFAULT_STOP_LOSS_PCT = o_stop
        sim_mod.DEFAULT_TAKE_PROFIT_PCT = o_tp
        sim_mod.TRAILING_STOP_PROFIT_LOCK_RATIO = o_ratio
        sim_mod.MAX_STOP_LOSS_PCT = o_max


def _composite_curve(champ_recs, chall_recs=None, limit_to_dates=None):
    """构建等权日组合收益。如果 limit_to_dates 非空，只有这些日期上的交易计入。"""
    if limit_to_dates:
        champ_recs = [r for r in champ_recs if r["trade_date"] in limit_to_dates]
        if chall_recs:
            chall_recs = [r for r in chall_recs if r["trade_date"] in limit_to_dates]

    c_by_date: dict[str, list[float]] = defaultdict(list)
    for r in champ_recs:
        c_by_date[r["exit_date"]].append(r["net_return"])

    t_by_date: dict[str, list[float]] = defaultdict(list) if chall_recs else None
    if chall_recs is not None:
        for r in chall_recs:
            t_by_date[r["exit_date"]].append(r["net_return"])

    # Union of all dates
    all_dates = sorted(set(c_by_date) | (set(t_by_date) if t_by_date else set()))
    c_equity = 1.0
    t_equity = 1.0
    peak_c = 1.0
    peak_t = 1.0
    mdd_c = 0.0
    mdd_t = 0.0
    c_daily = []
    t_daily = []

    for d in all_dates:
        rets_c = c_by_date.get(d, [])
        rets_t = t_by_date.get(d, []) if t_by_date else []

        dr_c = sum(rets_c) / len(rets_c) if rets_c else 0.0
        dr_t = sum(rets_t) / len(rets_t) if rets_t else 0.0

        c_daily.append(dr_c)
        t_daily.append(dr_t)

        c_equity *= 1 + dr_c / 100
        t_equity *= 1 + dr_t / 100
        peak_c = max(peak_c, c_equity)
        peak_t = max(peak_t, t_equity)
        mdd_c = min(mdd_c, (c_equity / peak_c - 1) * 100)
        mdd_t = min(mdd_t, (t_equity / peak_t - 1) * 100)

    c_snap = PerformanceSnapshot(
        average_net_return_pct=sum(r["net_return"] for r in champ_recs) / max(1, len(champ_recs)),
        cumulative_net_return_pct=(c_equity - 1) * 100,
        win_rate_pct=sum(1 for r in champ_recs if r["net_return"] > 0) / max(1, len(champ_recs)) * 100,
        profit_factor=_pf(champ_recs),
        max_drawdown_pct=mdd_c,
    )

    if chall_recs is not None:
        t_snap = PerformanceSnapshot(
            average_net_return_pct=sum(r["net_return"] for r in chall_recs) / max(1, len(chall_recs)),
            cumulative_net_return_pct=(t_equity - 1) * 100,
            win_rate_pct=sum(1 for r in chall_recs if r["net_return"] > 0) / max(1, len(chall_recs)) * 100,
            profit_factor=_pf(chall_recs),
            max_drawdown_pct=mdd_t,
        )
        return c_snap, t_snap, len(champ_recs), len(chall_recs), len(all_dates)
    return c_snap, len(champ_recs), len(all_dates)


def _pf(records):
    wins = sum(r["net_return"] for r in records if r["net_return"] > 0)
    loss = abs(sum(r["net_return"] for r in records if r["net_return"] < 0))
    return wins / loss if loss > 0 else float("inf")


def _bs(champ, chall):
    cm = {(r["code"], r["trade_date"]): r["net_return"] for r in champ}
    tm = {(r["code"], r["trade_date"]): r["net_return"] for r in chall}
    common = sorted(set(cm) & set(tm))
    if len(common) < 5:
        return {"paired_n": len(common), "error": "insufficient_common"}
    by_d: dict[str, list[float]] = defaultdict(list)
    for k in common:
        by_d[k[1]].append(tm[k] - cm[k])
    clusters = list(by_d.values())
    diffs = [v for c in clusters for v in c]
    rng = random.Random(20260731)
    boots = []
    for _ in range(10000):
        s = [clusters[rng.randrange(len(clusters))] for _ in range(len(clusters))]
        boots.append(sum(v for c in s for v in c) / sum(len(c) for c in s))
    boots.sort()
    return {
        "paired_n": len(common), "n_dates": len(clusters),
        "mean_diff": sum(diffs) / len(diffs),
        "ci95": [boots[250], boots[9750]],
    }


def main():
    db = SessionLocal()
    try:
        print("=" * 80)
        print("  PATH-A Score Line Grid v2 — 组合级 abstain-as-cash 测量")
        print("=" * 80)

        recs = list(db.scalars(
            select(Recommendation)
            .where(Recommendation.recommendation_id.like("btrec_%"),
                   Recommendation.trade_date >= "2025-04-08",
                   Recommendation.trade_date <= "2026-07-10")
        ).all())
        prod = [r for r in recs if _is_prod(r)]
        print(f"加载: {len(recs)} → 生产过滤: {len(prod)} ({len({r.trade_date for r in prod})} 日)")

        # Champion: all production-filtered recs
        print("\n🏆 Champion (sl=50, all):", end=" ", flush=True)
        t0 = time.perf_counter()
        champ = _sim_exits(prod)
        print(f"{len(champ)} trades ({time.perf_counter() - t0:.1f}s)")
        c_snap, c_n, c_dates_n = _composite_curve(champ)
        print(f"  WR={c_snap.win_rate_pct:.1f}% avg_net={c_snap.average_net_return_pct:+.4f}% "
              f"cum={c_snap.cumulative_net_return_pct:+.4f}% PF={c_snap.profit_factor:.4f} "
              f"MDD={c_snap.max_drawdown_pct:+.4f}% n={c_n} dates={c_dates_n}")

        # Challenge: various score_lines with composite-curve comparison
        score_lines = [65, 62, 60, 58, 55, 52]
        results = []

        for sl in score_lines:
            filtered = _filter_by_score_line(prod, sl)
            if len(filtered) < 3:
                print(f"\n⚔️  sl={sl}: {len(filtered)} recs → 跳过（太少）")
                continue

            t0 = time.perf_counter()
            chall = _sim_exits(filtered)
            elapsed = time.perf_counter() - t0
            # Get the trade_dates that challenger trades on
            chall_dates = {r["trade_date"] for r in chall}

            # Composite comparison: limit champion to same trade_dates
            c_comp, t_comp, cn_comp, tn_comp, nd_comp = _composite_curve(
                champ, chall, limit_to_dates=chall_dates
            )

            # Full champion (all dates, for abstain-as-cash reference)
            # Champion on challenger dates + challenger abstains cash on non-challenger dates
            all_dates = sorted(set(r["exit_date"] for r in champ) | set(r["exit_date"] for r in chall))
            c_by_exit: dict[str, list[float]] = defaultdict(list)
            for r in champ:
                c_by_exit[r["exit_date"]].append(r["net_return"])
            t_by_exit: dict[str, list[float]] = defaultdict(list)
            for r in chall:
                t_by_exit[r["exit_date"]].append(r["net_return"])

            eq_c = 1.0
            eq_t = 1.0
            pk_c = 1.0
            pk_t = 1.0
            mdd_c_ = 0.0
            mdd_t_ = 0.0
            for d in all_dates:
                rc = c_by_exit.get(d, [])
                rt = t_by_exit.get(d, [])
                drc = sum(rc) / len(rc) if rc else 0.0
                drt = sum(rt) / len(rt) if rt else 0.0
                eq_c *= 1 + drc / 100
                eq_t *= 1 + drt / 100
                pk_c = max(pk_c, eq_c)
                pk_t = max(pk_t, eq_t)
                mdd_c_ = min(mdd_c_, (eq_c / pk_c - 1) * 100)
                mdd_t_ = min(mdd_t_, (eq_t / pk_t - 1) * 100)

            c_full = PerformanceSnapshot(
                average_net_return_pct=sum(r["net_return"] for r in champ) / max(1, len(champ)),
                cumulative_net_return_pct=(eq_c - 1) * 100,
                win_rate_pct=sum(1 for r in champ if r["net_return"] > 0) / max(1, len(champ)) * 100,
                profit_factor=_pf(champ),
                max_drawdown_pct=mdd_c_,
            )
            t_full = PerformanceSnapshot(
                average_net_return_pct=sum(r["net_return"] for r in chall) / max(1, len(chall)),
                cumulative_net_return_pct=(eq_t - 1) * 100,
                win_rate_pct=sum(1 for r in chall if r["net_return"] > 0) / max(1, len(chall)) * 100,
                profit_factor=_pf(chall),
                max_drawdown_pct=mdd_t_,
            )
            decision = evaluate_challenger(c_full, t_full)
            bs = _bs(champ, chall)

            print(f"\n⚔️  sl={sl}: {len(filtered)} recs → {len(chall)} trades ({elapsed:.1f}s)")
            print(f"  {'指标':<28} {'Champion':>10} {'Challenger':>10} {'Δ':>12}")
            for lbl, cv, tv, fmt in [
                ("交易笔数", len(champ), len(chall), "d"),
                ("净胜率 %", c_full.win_rate_pct, t_full.win_rate_pct, ".2f"),
                ("平均净收益 %", c_full.average_net_return_pct, t_full.average_net_return_pct, ".4f"),
                ("累计净收益 %", c_full.cumulative_net_return_pct, t_full.cumulative_net_return_pct, ".4f"),
                ("Profit Factor", c_full.profit_factor, t_full.profit_factor, ".4f"),
                ("最大回撤 %", c_full.max_drawdown_pct, t_full.max_drawdown_pct, ".4f"),
            ]:
                d = tv - cv
                s = "+" if d > 0 else ""
                print(f"  {lbl:<28} {cv:{fmt}} {tv:{fmt}} {s}{d:{fmt}}")
            print(f"  Decision: {decision.decision}")
            if "error" not in bs:
                print(f"  Bootstrap: paired_n={bs['paired_n']} dates={bs['n_dates']} "
                      f"diff={bs['mean_diff']:+.6f}% CI=[{bs['ci95'][0]:+.6f}%, {bs['ci95'][1]:+.6f}%]")

            # By year
            by_year_data = {}
            for yr in ["2025", "2026"]:
                ch = [r for r in chall if r["trade_date"][:4] == yr]
                if len(ch) >= 3:
                    cp = [r for r in champ if r["trade_date"][:4] == yr]
                    # Composite curves limited to this year's challenger dates
                    cy_dates = {r["trade_date"] for r in ch}
                    cp_yr = [r for r in cp if r["trade_date"] in cy_dates]
                    if cp_yr and ch:
                        _, ty_snap, _, _, _ = _composite_curve(cp_yr, ch, limit_to_dates=cy_dates)
                        # Champion full-year for comparison
                        cy_snap, _, _ = _composite_curve(cp)
                        by_year_data[yr] = {
                            "champion_n": len(cp),
                            "champion_wr": round(cy_snap.win_rate_pct, 1),
                            "champion_avg": round(cy_snap.average_net_return_pct, 4),
                            "challenger_n": len(ch),
                            "challenger_wr": round(ty_snap.win_rate_pct, 1),
                            "challenger_avg": round(ty_snap.average_net_return_pct, 4),
                        }
            if by_year_data:
                print(f"  分年:")
                for yr, d in sorted(by_year_data.items()):
                    print(f"    {yr}: champ_n={d['champion_n']} WR={d['champion_wr']}% "
                          f"avg={d['champion_avg']:+.4f}% → "
                          f"chall_n={d['challenger_n']} WR={d['challenger_wr']}% "
                          f"avg={d['challenger_avg']:+.4f}%")

            results.append({
                "score_line": sl,
                "filtered_recs": len(filtered),
                "challenger_n": len(chall),
                "champion_n": len(champ),
                "decision": decision.to_dict(),
                "bootstrap": bs,
                "by_year": by_year_data,
            })

        # Summary table
        print(f"\n{'=' * 80}")
        print(f"  Summary: score_line grid scan")
        print(f"  {'sl':>5} {'recs':>6} {'n':>5} {'WR%':>7} {'avg%':>9} "
              f"{'cum%':>9} {'PF':>7} {'MDD%':>8} {'paired_n':>8} {'Decision':>12}")
        print(f"  {'─' * 5} {'─' * 6} {'─' * 5} {'─' * 7} {'─' * 9} "
              f"{'─' * 9} {'─' * 7} {'─' * 8} {'─' * 8} {'─' * 12}")
        c_snap_full, _, _ = _composite_curve(champ)
        print(f"  {'50':>5} {len(prod):>6} {len(champ):>5} "
              f"{c_snap_full.win_rate_pct:>6.1f}% "
              f"{c_snap_full.average_net_return_pct:>+8.4f}% "
              f"{c_snap_full.cumulative_net_return_pct:>+8.4f}% "
              f"{c_snap_full.profit_factor:>7.4f} "
              f"{c_snap_full.max_drawdown_pct:>+7.4f}% "
              f"{'—':>8} {'BASELINE':>12}")
        for r in results:
            bs = r["bootstrap"]
            print(f"  {r['score_line']:>5.0f} {r['filtered_recs']:>6} {r['challenger_n']:>5} "
                  f"{r['decision']['deltas'].get('win_rate_pct', 0):>+6.1f}% "
                  f"Δ{r['decision']['deltas'].get('average_net_return_pct', 0):>+8.4f}% "
                  f"Δ{r['decision']['deltas'].get('cumulative_net_return_pct', 0):>+8.4f}% "
                  f"Δ{r['decision']['deltas'].get('profit_factor', 0):>+7.4f} "
                  f"Δ{r['decision']['deltas'].get('max_drawdown_pct', 0):>+7.4f}% "
                  f"{bs.get('paired_n', 0):>8} {r['decision']['decision']:>12}")

        # Output
        out = {
            "experiment": "score_line_grid_path_a_v2",
            "champion_snapshot": c_snap_full.__dict__,
            "champion_n": len(champ),
            "champion_n_dates": len({r["trade_date"] for r in champ}),
            "challengers": results,
        }
        p = BACKEND_ROOT.parent / "output" / "si_score_line_grid_path_a_v2.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        print(f"\n📄 {p}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
