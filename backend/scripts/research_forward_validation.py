"""真·前瞻窗口验证（2026-02-09~2026-08-06，冻结快照之后、L-060 从未用过）。

数据：backtest run btr_fa625（26877 候选，score>=70，100% 因果成交，同一 _resolve_exit_causal）。
run 内自带的 market_regime 是旧分类器（结构性强势/强势，71% 高潮），与当前 _market_base 口径不符，
故按当前生产定义从 daily_kbars 重建每日 up/lu/ld 快照，再用 resolve_score_line_context 复现行政策。
对照：P0 现行 artifact / P72 统一线 / P0+弱势·高潮收紧到85。日度 top6 等权、无票=现金（L-015）。
披露：run 只保留 score>=70，无法检验 60/64 放松档（沿用 L-060 冻结窗口结论）。
"""
from __future__ import annotations
import json, sys, dataclasses
from pathlib import Path
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_limit_sentiment_panel import load_raw, build_daily_flags  # type: ignore
from scripts.research_intraday_portfolio_levers import _metric_summary, TOP_N  # type: ignore
from app.engine.aggregator import resolve_score_line_context, _market_base, _market_sentiment  # noqa
from app.engine.production_policy import DEFAULT_PRODUCTION_SELECTION_POLICY  # noqa

OUT = Path(__file__).resolve().parents[1] / "output" / "research"
RUN = "btr_fa625f3c41c147edbee3edb7d3adede5"
CACHE = OUT / "forward_trades.pkl"
BASE = 72.0


def _db_url():
    env = (Path(__file__).resolve().parents[1] / ".env").read_text()
    for line in env.splitlines():
        if line.startswith("ASHARE_DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("no db url")


def build_forward():
    if CACHE.exists():
        return pd.read_pickle(CACHE)
    eng = create_engine(_db_url())
    q = text("""SELECT a.signal_date, a.code,
                      JSON_EXTRACT(a.signal_json,'$.score') score,
                      JSON_EXTRACT(t.trade_json,'$.net_return_pct') net
               FROM backtest_candidates a JOIN backtest_trades t
               ON a.backtest_id=t.backtest_id AND a.signal_date=t.signal_date AND a.code=t.code
               WHERE a.backtest_id=:r""")
    with eng.connect() as c:
        df = pd.DataFrame(c.execute(q, {"r": RUN}).mappings().all())
    df["score"] = df.score.astype(float); df["net"] = df.net.astype(float)
    df.to_pickle(CACHE)
    return df


def build_market_snapshots(dates):
    raw = load_raw("2026-01-15")
    fl = build_daily_flags(raw)
    fl = fl[fl.tradable_univ]
    snap = {}
    for d, gp in fl.groupby("trade_date"):
        snap[d] = {"sample_count": len(gp),
                   "up_count": int((gp.pct_chg > 0).sum()),
                   "limit_up_count": int(gp.sealed_up.sum()),
                   "limit_down_count": int(gp.sealed_dn.sum())}
    return snap


def policy_variant(weak_climax_line=None):
    if weak_climax_line is None:
        return DEFAULT_PRODUCTION_SELECTION_POLICY
    ov = dict(DEFAULT_PRODUCTION_SELECTION_POLICY.regime_sentiment_line_overrides)
    ov["弱势·高潮"] = float(weak_climax_line)
    return dataclasses.replace(DEFAULT_PRODUCTION_SELECTION_POLICY, regime_sentiment_line_overrides=tuple(ov.items()))


def daily_cohorts(tr, snaps, policy):
    rows = []
    for d, gp in tr.groupby("signal_date"):
        s = snaps.get(d)
        ctx = resolve_score_line_context(BASE, s, "balanced", policy)
        eff = ctx["effective_score_line"]; key = f"{ctx['market_base']}·{ctx['market_sentiment']}"
        top = gp[gp.score >= eff].sort_values("score", ascending=False).head(TOP_N)
        rows.append({"signal_date": d, "basket": top.net.mean() if len(top) else 0.0,
                     "n": len(top), "key": key, "eff": eff,
                     "base": ctx["market_base"], "sent": ctx["market_sentiment"]})
    return pd.DataFrame(rows).sort_values("signal_date")


def show(name, d):
    m = _metric_summary(d.basket)
    print(f"[{name:16s}] days{len(d):>3} 出票{(d.n>0).sum():>3}(0票{(d.n==0).mean()*100:4.1f}%) "
          f"avg{m['avg_cohort_return_pct']:+.4f} cum{m['cumulative_pct']:+.2f}% MDD{m['max_drawdown_pct']:6.2f}% "
          f"posDay{m['positive_day_pct']:.1f}%")
    return m


def forward_combo_map(tr, snaps):
    print("\n===== 前瞻窗口 当前口径 16 格 × 分数桶 均净%（胜率/n）=====")
    rows = []
    for d, gp in tr.groupby("signal_date"):
        s = snaps.get(d); b = _market_base(s); se = _market_sentiment(s)
        g = gp.copy(); g["base"] = b; g["sent"] = se; rows.append(g)
    x = pd.concat(rows)
    buckets = [(70,72),(72,76),(76,82),(82,101)]
    for b in ["强牛","偏多","震荡","弱势"]:
        for se in ["高潮","活跃","平淡","冰点"]:
            sub = x[(x.base==b)&(x.sent==se)]
            if len(sub) < 15: continue
            line=f"  {b}·{se:2s}(n{len(sub):>4}) "
            for lo,hi in buckets:
                z=sub[(sub.score>=lo)&(sub.score<hi)]
                if len(z): line+=f"[{lo}-{hi if hi<101 else '+'}]{z.net.mean():+.2f}/{int((z.net>0).mean()*100):02d}/n{len(z):<4} "
            print(line)
    return x


def monthly(name, d):
    m = d.assign(mon=d.signal_date.str[:7]).groupby("mon").basket.mean()
    print(f"  {name} 逐月均净:", {k: round(v,3) for k,v in m.items()}, "为正月占比", round((m>0).mean(),2))


def main():
    tr = build_forward()
    dates = sorted(tr.signal_date.unique())
    snaps = build_market_snapshots(dates)
    have = [d for d in dates if d in snaps]
    tr = tr[tr.signal_date.isin(have)]
    print(f"forward window {dates[0]}..{dates[-1]} trades={len(tr)} days={tr.signal_date.nunique()} (有市场快照)")
    x = forward_combo_map(tr, snaps)
    pols = {"P0_current": policy_variant(None),
            "P72_uniform": None,
            "P0+weakClimax85": policy_variant(85.0)}
    print("\n===== 前瞻组合对照 =====")
    res = {}
    # P72: every combo line 72 → build via a policy whose adjustments zeroed and no overrides/cap
    p72 = dataclasses.replace(DEFAULT_PRODUCTION_SELECTION_POLICY,
                              market_base_score_adjustments=(("强牛",0),("偏多",0),("震荡",0),("弱势",0)),
                              sentiment_score_adjustments=(("高潮",0),("活跃",0),("平淡",0),("冰点",0)),
                              climax_nonweak_extra=0.0, weak_regime_score_cap=None,
                              regime_sentiment_line_overrides=())
    pols["P72_uniform"] = p72
    for name, pol in pols.items():
        d = daily_cohorts(tr, snaps, pol); res[name] = d
        show(name, d); monthly(name, d)
    # combo day breakdown P0
    d0 = res["P0_current"]
    print("\nP0 前瞻日度 combo 分解:")
    for k,g in d0.groupby("key"):
        print(f"  {k:9s} days{len(g):>3} 出票{(g.n>0).sum():>3} 均{g.basket.mean():+.4f} 盈天{(g.basket>0).mean()*100:4.1f}%")
    res["P0_current"].to_pickle(OUT/"forward_P0_daily.pkl")


if __name__ == "__main__":
    main()
