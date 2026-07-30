"""Self-improve T-trailing-lock-v2: 围绕当前生产 champion (profit_floor@0.50, horizon=5)
放宽/收紧 trailing 锁利比例的诚实配对回测（20cm-only 板块口径）。

主假设：如果生产 profit_floor@0.50 锁利过早、把浮盈回吐（self_check 标记 trailing_stop 簇
未吃高点 +5.64%），那么调宽 profit_lock_ratio（容忍更大回撤）后，平均单笔净收益应改善且
胜率/PF/max_loss 不退化。

用法：
  backend/.venv/bin/python backend/scripts/si_trailing_lock_widen_v2.py
"""

from __future__ import annotations

import json
import random
import sys
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
)

SCORE_LINE_FLOOR = 72.0
NET_COST_PCT = 0.07

CHAMPION_RATIO = 0.50  # 生产 TRAILING_STOP_PROFIT_LOCK_RATIO
CHALLENGER_RATIOS = [0.30, 0.40, 0.60, 0.70, 0.80, 0.90]

BOOTSTRAP_N = 10000
BOOTSTRAP_SEED = 20260730
HORIZON = 5


def _is_20cm(code: str) -> bool:
    return str(code or "").startswith(("300", "301", "688", "689"))


def _make_exit_resolver(profit_lock_ratio=None, *, policy="profit_floor", callback_pct=None):
    def resolver(holding_bars, stop_loss_price, take_profit_price, *, code="", entry_price=0.0):
        kw = dict(
            holding_bars=holding_bars,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            code=code,
            entry_price=entry_price,
            dynamic_trailing=True,
            trailing_policy=policy,
        )
        if policy == "profit_floor":
            kw["profit_lock_ratio"] = profit_lock_ratio
        else:
            kw["profit_lock_ratio"] = 1.0
            import app.services.outcome_simulation as _sim
            _sim.TRAILING_STOP_CALLBACK_PCT = callback_pct
        return _resolve_exit_causal(**kw)
    return resolver


PEAK_CALLBACK_PCTS = [1.5, 2.0, 3.0]


def _replay(db, profit_lock_ratio=None, *, policy="profit_floor", callback_pct=None):
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

    resolver = _make_exit_resolver(profit_lock_ratio, policy=policy, callback_pct=callback_pct)
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
    dates = sorted(by_date)
    clusters = [by_date[d] for d in dates]
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
    return dict(paired_n=len(common), n_dates=len(dates), mean_diff=round(mean_diff,4),
                ci95=[round(lo,4),round(hi,4)], ci_excludes_zero=bool(lo>0 or hi<0))


def main():
    db = SessionLocal()
    try:
        print(f"[config] champion={CHAMPION_RATIO} challengers={CHALLENGER_RATIOS} horizon={HORIZON} board=20cm-only")
        champion = _replay(db, CHAMPION_RATIO)
        m_champ = _metrics(champion)
        print(f"\n=== champion profit_floor@{CHAMPION_RATIO} (20cm-only) ===")
        print(f"  {m_champ}")

        self_chk = _replay(db, CHAMPION_RATIO)
        chk_eq = all(abs(a["net_return"]-b["net_return"])<1e-6 for a,b in zip(champion,self_chk)) and len(champion)==len(self_chk)
        print(f"  identity check: {chk_eq} (n={len(champion)})")
        if not chk_eq:
            print("  ⚠️ FAILED, aborting")
            return

        ec = defaultdict(list)
        for r in champion:
            ec[r["exit_reason"]].append(r["net_return"])
        print("  exit breakdown:")
        for reason, vals in sorted(ec.items(), key=lambda kv: sum(kv[1])):
            print(f"    {reason:34s} n={len(vals):3d} avg={sum(vals)/len(vals):+.2f}%")

        print("\n=== challengers (profit_floor) ===")
        results = []
        for ratio in CHALLENGER_RATIOS:
            chall = _replay(db, ratio)
            m = _metrics(chall)
            boot = _paired_bootstrap_diff(champion, chall)
            ct = [r["net_return"] for r in chall if r["exit_reason"].startswith("trailing_stop")]
            champ_t = [r["net_return"] for r in champion if r["exit_reason"].startswith("trailing_stop")]
            t_avg = (sum(ct)/len(ct)) if ct else None
            print(f"\n  ratio={ratio}: {m}")
            print(f"    trailing cluster: n={len(ct)} avg={t_avg:+.2f}% (champ {(sum(champ_t)/len(champ_t)):.2f}%)" if t_avg else "")
            print(f"    paired bootstrap: {boot}")
            results.append(dict(policy="profit_floor", profit_lock_ratio=ratio, metrics=m, paired_bootstrap=boot))

        print("\n=== challengers (peak_callback) ===")
        import app.services.outcome_simulation as _sim
        orig_cb = _sim.TRAILING_STOP_CALLBACK_PCT
        try:
            for cb in PEAK_CALLBACK_PCTS:
                chall = _replay(db, None, policy="peak_callback", callback_pct=cb)
                m = _metrics(chall)
                boot = _paired_bootstrap_diff(champion, chall)
                print(f"\n  callback={cb}%: {m}")
                print(f"    paired bootstrap: {boot}")
                results.append(dict(policy="peak_callback", callback_pct=cb, metrics=m, paired_bootstrap=boot))
        finally:
            _sim.TRAILING_STOP_CALLBACK_PCT = orig_cb

        out = dict(generated_at=datetime.now(timezone.utc).isoformat(), label="backtest",
                   config=dict(champion_ratio=CHAMPION_RATIO, challenger_ratios=CHALLENGER_RATIOS,
                               horizon=HORIZON, board="20cm-only", bootstrap_n=BOOTSTRAP_N, seed=BOOTSTRAP_SEED),
                   champion_metrics=m_champ, challengers=results, identity_check_passed=chk_eq)
        out_path = BACKEND_ROOT.parent / "output" / "trailing_lock_widen_v2_20260731.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        print(f"\n[written] {out_path}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
