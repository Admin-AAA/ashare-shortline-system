"""第三段真前瞻窗 2026-08-07~09-02（旧 canonical run 止于 08-05，本段从未分析）。
新 run btr_2bec6fed 与旧 run 在 22361 个重叠键净收益完全一致（同一因果引擎超集）。
(a) v1 vs v2 生产同构对照，验证现行 v2 在未见数据上是否仍优；
(b) 附 regime(base·sent) 与前一日 regime，做"状态转移"新机制探索（日线可得、跨窗）。
只读，不写生产。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.production_policy import load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext
from app.engine.aggregator import _market_base, _market_sentiment
from scripts.research_forward_validation import build_market_snapshots
OUT = B / "output" / "research"
RUN3 = "btr_2bec6fed6c344eaa819e533b8220a0d6"
D0, D1 = "2026-08-07", "2026-09-02"


def db():
    for line in (B / ".env").read_text().splitlines():
        if line.startswith("ASHARE_DATABASE_URL="):
            return create_engine(line.split("=", 1)[1].strip().strip('"').strip("'"))


def load_run3():
    cache = OUT / "run3_canonical.pkl"
    if cache.exists(): return pd.read_pickle(cache)
    q = text("""SELECT a.signal_date dt, a.code,
               JSON_EXTRACT(a.signal_json,'$.score') score,
               JSON_UNQUOTE(JSON_EXTRACT(a.signal_json,'$.industry')) industry,
               JSON_EXTRACT(t.trade_json,'$.net_return_pct') net
               FROM backtest_candidates a JOIN backtest_trades t
               ON a.backtest_id=t.backtest_id AND a.signal_date=t.signal_date AND a.code=t.code
               WHERE a.backtest_id=:r AND a.signal_date BETWEEN :d0 AND :d1""")
    df = pd.read_sql(q, db(), params={"r": RUN3, "d0": D0, "d1": D1})
    df["score"] = df.score.astype(float); df["net"] = df.net.astype(float)
    df["industry"] = df.industry.fillna("未知"); df["dt"] = df.dt.astype(str)
    df.to_pickle(cache); return df


def select(fw, snaps, policy):
    rows = []
    for d, gp in fw.groupby("dt"):
        snap = snaps.get(d)
        cards = [{"code": str(r.code), "final_score": float(r.score), "industry": str(r.industry), "signal_price": 10.0}
                 for r in gp.itertuples()]
        res = evaluate_selection_policy(cards, SelectionPolicyContext(72., 10, dict(snap or {})), policy)
        net = {str(r.code): float(r.net) for r in gp.itertuples()}
        b = _market_base(snap); se = _market_sentiment(snap)
        for c in res.recommendations:
            code = str(c.get("code"))
            if code in net:
                rows.append({"dt": d, "code": code, "net": net[code], "key": f"{b}·{se}"})
    return pd.DataFrame(rows)


def stats(df, all_dates, label):
    if not len(df): print(f"  {label}: 空"); return
    avg = df.net.mean(); win = (df.net > 0).mean()
    # 盈亏比 PF
    pos, neg = df.net[df.net > 0].sum(), -df.net[df.net < 0].sum()
    pf = pos / neg if neg > 0 else float("nan")
    coh = df.groupby("dt").net.mean().reindex(all_dates).fillna(0)
    cum = coh.sum(); mdd = (coh.cumsum().cummax() - coh.cumsum()).max()
    print(f"  {label}: n={len(df)} 逐笔{avg:+.4f} 胜{win:.3f} PF{pf:.3f} | 日cum{cum:+.2f} MDD-{mdd:.2f} 日均{coh.mean():+.4f} 出票天{(coh!=0).sum()}")
    return dict(avg=avg, win=win, pf=pf, cum=cum, mdd=mdd)


def main():
    fw = load_run3()
    dates = sorted(fw.dt.unique()); snaps = build_market_snapshots(dates)
    have = [d for d in dates if d in snaps]
    miss = [d for d in dates if d not in snaps]
    print(f"第三窗 {D0}~{D1}: {len(dates)}日, 有市场快照{len(have)} 缺{miss}")
    fw = fw[fw.dt.isin(have)]
    v1 = load_production_selection_policy(version=1); v2 = load_production_selection_policy(version=2)
    s1 = select(fw, snaps, v1); s2 = select(fw, snaps, v2)
    ad = sorted(set(s1.dt) | set(s2.dt))
    print("\n== v1 vs v2（未见窗）==")
    stats(s1, ad, "v1"); stats(s2, ad, "v2")
    # regime distribution of v2 selected
    print("\n== v2 选中逐笔按格 ==")
    for k, g in s2.groupby("key"):
        print(f"  {k}: n{len(g)} 均{g.net.mean():+.3f} 胜{(g.net>0).mean():.2f}")
    # transition: today key vs previous trading-day key
    daykey = s2[["dt", "key"]].drop_duplicates().sort_values("dt")
    alld = sorted(snaps.keys()); alld = [d for d in alld if d in have]
    prev = {d: (f"{_market_base(snaps[alld[i-1]])}·{_market_sentiment(snaps[alld[i-1]])}" if i > 0 else None)
            for i, d in enumerate(alld)}
    s2["prev_key"] = s2.dt.map(prev)
    s2["transition"] = s2.prev_key.fillna("起点") + "→" + s2.key
    print("\n== v2 选中按【状态转移】（前一日regime→当日regime）==")
    t = s2.groupby("transition").net.agg(n="size", avg="mean", win=lambda x: (x > 0).mean())
    t = t.sort_values("avg")
    print(t.round(3).to_string())
    s2.to_pickle(OUT / "run3_v2_selected.pkl")


if __name__ == "__main__":
    main()
