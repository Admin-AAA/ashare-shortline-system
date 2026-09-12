"""pm_ret 尾盘追高否决的【第三真前瞻窗 08-07~09-02】生产同构 canonical（L-071/L-077，只读）。

解锁 L-077 的前置数据任务后（backfill_5min_codes.py 回填 run3 候选全市场 5min），
对第三窗跑两次真实 evaluate_selection_policy：baseline 全卡 vs 排序前硬否决
(cell∈{弱势·活跃,强牛·高潮} 且 pm_ret>T)，第 11 名递补；join run3 canonical 净收益。
pm_ret 直接由 minute_kbars 5min 每 3 根重采样 15min 计算（=15:00收/13:15开-1，%），
缺 pm_ret 的卡保守保留。输出逐笔/日cohort/配对日差 bootstrap CI/被砍票，供与冻结、前瞻三窗合判。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import text
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.production_policy import (
    load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext)
from app.engine.aggregator import _market_base, _market_sentiment
from scripts.research_canonical_two_window import (
    build_market_snapshots, trade_metrics, daily_cohort, cohort_metrics, _bootstrap_ci)
from scripts.research_third_window import load_run3, D0, D1
CELLS = {"弱势·活跃", "强牛·高潮"}
OUT = B / "output" / "research"


def build_pm_lookup(eng, pairs: pd.DataFrame) -> dict:
    look = {}
    dates = sorted(pairs.dt.unique())
    for m in sorted({str(d)[:7] for d in dates}):
        md = [d for d in dates if str(d).startswith(m)]
        codes = sorted(pairs[pairs.dt.isin(md)].code.unique().tolist())
        if not codes:
            continue
        q = text("""SELECT trade_date dt, code, trade_time tt, open o, close c
                    FROM minute_kbars WHERE freq='5min' AND trade_date BETWEEN :d0 AND :d1 AND code IN :c
                    ORDER BY code, trade_time""")
        df = pd.read_sql(q, eng, params={"d0": min(md), "d1": max(md), "c": tuple(codes)})
        df["dt"] = df.dt.astype(str); df["code"] = df.code.astype(str)
        for (dt, code), sub in df.groupby(["dt", "code"], sort=False):
            sub = sub.sort_values("tt").reset_index(drop=True)
            g = sub.groupby(sub.index // 3)
            o = g.o.first().values; c = g.c.last().values
            if len(c) < 9:
                continue
            if not o[8] or o[8] <= 0:
                continue
            look[(dt, code)] = (c[-1] / o[8] - 1) * 100
        print(f"  5min {m}: cum lookups={len(look)}", flush=True)
    return look


def arms(fw, snaps, pm, T):
    pol = load_production_selection_policy(version=2)
    base, veto, cut = [], [], []
    for d, gp in fw.groupby("dt"):
        snap = snaps[d]; key = f"{_market_base(snap)}·{_market_sentiment(snap)}"
        cards = [{"code": str(r.code), "final_score": float(r.score), "industry": str(r.industry),
                  "signal_price": 10.0} for r in gp.itertuples()]
        netmap = {str(r.code): float(r.net) for r in gp.itertuples()}
        def toxic(c, key=key):
            v = pm.get((d, str(c.get("code"))))
            return v is not None and key in CELLS and v > T
        r0 = evaluate_selection_policy(cards, SelectionPolicyContext(72., 10, dict(snap)), pol)
        r1 = evaluate_selection_policy([c for c in cards if not toxic(c)],
                                       SelectionPolicyContext(72., 10, dict(snap)), pol)
        s0 = {str(c.get("code")) for c in r0.recommendations}
        s1 = {str(c.get("code")) for c in r1.recommendations}
        for codes, rows in ((s0, base), (s1, veto)):
            for code in codes:
                if code in netmap:
                    rows.append({"dt": d, "code": code, "net": netmap[code], "key": key})
        for code in s0 - s1:
            if code in netmap:
                cut.append({"dt": d, "code": code, "net": netmap[code], "key": key})
    return map(pd.DataFrame, (base, veto, cut))


def report(win, base, veto, cut, all_dates):
    tb, tv = trade_metrics(base), trade_metrics(veto)
    cb, cv = daily_cohort(base, all_dates), daily_cohort(veto, all_dates)
    mb, mv = cohort_metrics(cb), cohort_metrics(cv)
    chg = cv - cb; ch = chg[chg != 0]; lo, hi = _bootstrap_ci(ch.values)
    monthly = chg.groupby(lambda d: d[:7]).sum()
    print(f" [{win}] 逐笔 n{tb['n']}->{tv['n']} avg{tb['avg_net']:+.4f}->{tv['avg_net']:+.4f} "
          f"win{tb['win']:.3f}->{tv['win']:.3f} PF{tb['pf']}->{tv['pf']}")
    print(f"        日cohort 日均{mb['daily_avg']:+.4f}->{mv['daily_avg']:+.4f} "
          f"MDD{mb['mdd']:+.2f}->{mv['mdd']:+.2f}")
    print(f"        配对变化日{len(ch)} Δ均{ch.mean():+.4f} CI[{lo:+.4f},{hi:+.4f}] "
          f"better-day{(ch > 0).mean():.2f} 月正{(monthly > 0).sum()}-{(monthly < 0).sum()}")
    if len(cut):
        print(f"        被砍{len(cut)}笔 均{cut.net.mean():+.4f} win{(cut.net > 0).mean():.3f} " +
              " ".join(f"{k}{g.net.mean():+.2f}/n{len(g)}" for k, g in cut.groupby("key")))


def main():
    from app.db.session import SessionLocal
    fw = load_run3()
    dates = sorted(fw.dt.unique()); snaps = build_market_snapshots(dates)
    have = [d for d in dates if d in snaps]
    miss = [d for d in dates if d not in snaps]
    fw = fw[fw.dt.isin(have)]
    print(f"第三窗 {D0}~{D1}: {len(dates)}日 有快照{len(have)} 缺{miss} 候选{len(fw)}")
    db = SessionLocal()
    try:
        from sqlalchemy import create_engine
        eng = create_engine(__import__("os").environ.get("ASHARE_DATABASE_URL") or
                            next(l.split("=", 1)[1].strip().strip('"').strip("'")
                                 for l in (B / ".env").read_text().splitlines()
                                 if l.startswith("ASHARE_DATABASE_URL=")))
    finally:
        db.close()
    pm = build_pm_lookup(eng, fw[["dt", "code"]].drop_duplicates())
    print(f"pm_ret lookup run3 = {len(pm)} (候选对 {len(fw[['dt','code']].drop_duplicates())})")
    for T in (1.25, 1.5, 1.75):
        print(f"\n================= RUN3 pm_ret 阈值 T={T} =================")
        b, v, c = arms(fw, snaps, pm, T)
        report("RUN3", b, v, c, sorted(set(b.dt) | set(v.dt)))


if __name__ == "__main__":
    main()
