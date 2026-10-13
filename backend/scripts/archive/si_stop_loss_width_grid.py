"""Self-improve T-stop-loss-width: 20cm-only 止损宽度诚实配对网格。

主假设：生产 DEFAULT_STOP_LOSS_PCT=-5.5% 对 20cm 股票过紧——止损坏 n=64(36 hit +28 gap)
占总亏损最大头，其中 hit 簇 n=36 avg=-5.69% 精确锚在止损边界，价格触 -5.5% 后反弹→止损过紧典型特征。
放宽止损给足 20cm 波动空间，应减少误杀、提高 avg_net，且胜率/PF/max_loss 不退化。

证据路径：backtest，20cm-only clean>=72 官方口径，冻结入场/止盈/trailing 0.30/其他参数，
只改变固定止损百分比。

用法：
  backend/.venv/bin/python backend/scripts/si_stop_loss_width_grid.py
"""

from __future__ import annotations

import json, random, sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.tables import DailyKbar, Recommendation, RecommendationRun
from app.services.official_sample_scope import official_sample_conditions
from app.services.outcome_simulation import (
    _resolve_exit_causal,
    _simulate_outcome,
    TRAILING_STOP_ACTIVATE_PCT,
    TRAILING_STOP_CALLBACK_PCT,
    TRAILING_STOP_MIN_LOCK_PCT,
    TRAILING_STOP_PROFIT_LOCK_RATIO,
)

SCORE_LINE_FLOOR = 72.0
NET_COST_PCT = 0.07
HORIZON = 5

# Champion: 生产 DEFAULT_STOP_LOSS_PCT = -5.5
CHAMPION_STOP_PCT = -5.5
CHALLENGER_STOP_PCTS = [-4.5, -6.5, -7.0, -7.5, -8.0, -10.0]

BOOTSTRAP_N = 10000
BOOTSTRAP_SEED = 20260731


def _is_20cm(code: str) -> bool:
    return str(code or "").startswith(("300", "301", "688", "689"))


def _make_stop_overriding_resolver(desired_stop_pct: float):
    """返回 exit_resolver，用 desired_stop_pct 覆写止损价但保留止盈和 trailing 逻辑。"""
    def resolver(holding_bars, stop_loss_price, take_profit_price, *, code="", entry_price=0.0):
        # 覆写止损：entry_price * (1 + desired_stop_pct/100)
        if entry_price > 0:
            custom_stop = entry_price * (1 + desired_stop_pct / 100.0)
        else:
            custom_stop = stop_loss_price
        return _resolve_exit_causal(
            holding_bars=holding_bars,
            stop_loss_price=custom_stop,
            take_profit_price=take_profit_price,
            code=code,
            entry_price=entry_price,
            dynamic_trailing=True,
            trailing_policy="profit_floor",
            profit_lock_ratio=TRAILING_STOP_PROFIT_LOCK_RATIO,
        )
    return resolver


def _replay(db, stop_pct: float) -> list[dict]:
    recs = db.scalars(
        select(Recommendation)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .where(*official_sample_conditions())
        .where(Recommendation.final_score >= SCORE_LINE_FLOOR)
        .order_by(Recommendation.trade_date.asc())
    ).all()
    recs = [r for r in recs if _is_20cm(str(r.code))]

    codes = sorted({str(r.code) for r in recs})
    bars_by_code = defaultdict(list)
    for bar in db.scalars(
        select(DailyKbar).where(DailyKbar.code.in_(codes)).order_by(
            DailyKbar.code.asc(), DailyKbar.trade_date.asc()
        )
    ).all():
        bars_by_code[str(bar.code)].append(bar)

    resolver = _make_stop_overriding_resolver(stop_pct)
    records = []
    for rec in recs:
        code = str(rec.code)
        bars = bars_by_code.get(code) or []
        dates = [str(b.trade_date) for b in bars]
        if not bars:
            continue
        sim = _simulate_outcome(rec, bars, dates, HORIZON, exit_resolver=resolver)
        if not sim or not sim.get("entry_triggered"):
            continue
        label = sim.get("outcome_label")
        if label not in ("success", "failed"):
            continue
        gross = float(sim.get("executed_return") or 0.0)
        records.append(dict(
            code=code, trade_date=str(rec.trade_date),
            exit_reason=sim.get("exit_reason"), gross_return=gross,
            net_return=gross - NET_COST_PCT,
        ))
    return records


def _metrics(records):
    rets = [r["net_return"] for r in records]
    if not rets:
        return {"n": 0}
    wins = sum(1 for x in rets if x > 0)
    losses = [x for x in rets if x < 0]
    gw = sum(x for x in rets if x > 0)
    gl = -sum(losses)
    return dict(
        n=len(rets), win_rate=round(wins/len(rets)*100,2),
        avg_net=round(sum(rets)/len(rets),4), sum_net=round(sum(rets),2),
        avg_win=round(gw/wins,4) if wins else None,
        avg_loss=round(-gl/len(losses),4) if losses else None,
        profit_factor=round(gw/gl,3) if gl>0 else None,
        max_loss=round(min(rets),2),
    )


def _paired_bootstrap_diff(champ, chall):
    def key(r):
        return (r["code"], r["trade_date"])
    cmap = {key(r): r["net_return"] for r in champ}
    smap = {key(r): r["net_return"] for r in chall}
    common = sorted(set(cmap) & set(smap))
    if not common:
        return {"paired_n": 0}
    by_date = defaultdict(list)
    for k in common:
        by_date[k[1]].append(smap[k] - cmap[k])
    dates_set = sorted(by_date)
    clusters = [by_date[d] for d in dates_set]
    diffs = [smap[k]-cmap[k] for k in common]
    mean_diff = sum(diffs)/len(diffs)
    rng = random.Random(BOOTSTRAP_SEED)
    boot_means = []
    for _ in range(BOOTSTRAP_N):
        sample = [clusters[rng.randrange(len(clusters))] for _ in range(len(clusters))]
        flat = [x for c in sample for x in c]
        boot_means.append(sum(flat)/len(flat))
    boot_means.sort()
    lo = boot_means[int(0.025*BOOTSTRAP_N)]
    hi = boot_means[int(0.975*BOOTSTRAP_N)]
    return dict(paired_n=len(common), n_dates=len(dates_set), mean_diff=round(mean_diff,4),
                ci95=[round(lo,4),round(hi,4)], ci_excludes_zero=bool(lo>0 or hi<0))


def main():
    db = SessionLocal()
    try:
        print(f"[config] champion_stop={CHAMPION_STOP_PCT}% challengers={CHALLENGER_STOP_PCTS} "
              f"horizon={HORIZON} board=20cm-only trailing=profit_floor@{TRAILING_STOP_PROFIT_LOCK_RATIO}")

        champion = _replay(db, CHAMPION_STOP_PCT)
        m_champ = _metrics(champion)
        print(f"\n=== champion stop={CHAMPION_STOP_PCT}% ===")
        print(f"  {m_champ}")

        # 对称性自检
        self_chk = _replay(db, CHAMPION_STOP_PCT)
        chk_eq = all(abs(a["net_return"]-b["net_return"])<1e-6 for a,b in zip(champion,self_chk)) and len(champion)==len(self_chk)
        print(f"  identity check: {chk_eq} (n={len(champion)})")
        if not chk_eq:
            print("  ⚠️ FAILED, aborting")
            return

        # 止损簇分析
        print("  stop cluster breakdown:")
        for label, prefix in [("stop_loss_hit", "stop_loss_hit"), ("stop_loss_gap", "stop_loss_gap"),
                               ("stop_loss_intraday", "stop_loss_intraday")]:
            cluster = [r for r in champion if r["exit_reason"].startswith(prefix)]
            if cluster:
                avg = sum(r["net_return"] for r in cluster)/len(cluster)
                print(f"    {label:30s} n={len(cluster):3d} avg={avg:+.2f}%")

        # 其他出场
        ec = defaultdict(list)
        for r in champion:
            ec[r["exit_reason"]].append(r["net_return"])
        print("  full exit breakdown:")
        for reason, vals in sorted(ec.items(), key=lambda kv: sum(kv[1])):
            print(f"    {reason:34s} n={len(vals):3d} avg={sum(vals)/len(vals):+.2f}% sum={sum(vals):+.1f}%")

        results = []
        print("\n=== challengers ===")
        for sp in CHALLENGER_STOP_PCTS:
            chall = _replay(db, sp)
            m = _metrics(chall)
            boot = _paired_bootstrap_diff(champion, chall)

            # 止损簇
            sl_hit = [r for r in chall if r["exit_reason"] == "stop_loss_hit"]
            sl_gap = [r for r in chall if r["exit_reason"] == "stop_loss_gap"]
            sl_hit_avg = sum(r["net_return"] for r in sl_hit)/len(sl_hit) if sl_hit else None
            sl_gap_avg = sum(r["net_return"] for r in sl_gap)/len(sl_gap) if sl_gap else None

            # max_loss 分析
            print(f"\n  stop={sp}%: {m}")
            print(f"    stop_loss_hit: n={len(sl_hit)} avg={sl_hit_avg:+.2f}%" if sl_hit_avg is not None else f"    stop_loss_hit: n=0")
            print(f"    stop_loss_gap: n={len(sl_gap)} avg={sl_gap_avg:+.2f}%" if sl_gap_avg is not None else f"    stop_loss_gap: n=0")
            print(f"    paired bootstrap: {boot}")

            results.append(dict(stop_pct=sp, metrics=m, paired_bootstrap=boot,
                                stop_loss_hit_n=len(sl_hit), stop_loss_hit_avg=sl_hit_avg,
                                stop_loss_gap_n=len(sl_gap), stop_loss_gap_avg=sl_gap_avg))

        out = dict(generated_at=datetime.now(timezone.utc).isoformat(), label="backtest",
                   config=dict(champion_stop_pct=CHAMPION_STOP_PCT, challenger_stop_pcts=CHALLENGER_STOP_PCTS,
                               horizon=HORIZON, board="20cm-only", trailing=f"profit_floor@{TRAILING_STOP_PROFIT_LOCK_RATIO}",
                               bootstrap_n=BOOTSTRAP_N, seed=BOOTSTRAP_SEED),
                   champion_metrics=m_champ, challengers=results, identity_check_passed=chk_eq)
        out_path = BACKEND_ROOT.parent / "output" / "stop_loss_width_grid_20260731.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        print(f"\n[written] {out_path}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
