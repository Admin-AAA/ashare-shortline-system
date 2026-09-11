"""聚焦验证：收紧「弱势·高潮」毒区的组合级因果收益（只读）。

审计发现 弱势·高潮 在训练/OOS 全分档为负、逐月为正比例仅 0.2，却被 weak cap=60 放行。
本脚本：
1. 日度 cohort 按 combo 分解，看 弱势·高潮 日 basket 的均净/胜率/月份分布（单日聚集 L-039）；
2. 反事实：P0 现行 vs 把「弱势·高潮」线上调到 79 / 85（走 regime_sentiment_line_overrides，
   最高优先级、天然绕过 weak cap），组合级对照（L-015，无票=现金）；
3. 三段时间符号稳健性；训练/OOS 分列；被拦日的机会成本符号（应为避损=被拦日均净<0）。
"""
from __future__ import annotations
import json, sys, pickle
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_intraday_portfolio_levers import _metric_summary, TOP_N  # type: ignore

OUT = Path(__file__).resolve().parents[1] / "output" / "research"
TR = pd.read_pickle(OUT / "regime_scoreline_trades.pkl")
DAYCTX = pickle.loads((OUT / "regime_scoreline_dayctx.pkl").read_bytes())
dates = sorted(TR.signal_date.unique()); SPLIT = dates[int(len(dates)*0.6)-1]


def daily_baskets(weak_climax_line: float | None):
    """weak_climax_line=None 用现行每日线；否则把 弱势·高潮 固定为该线。"""
    rows = []
    for date, gp in TR.groupby("signal_date"):
        ctx = DAYCTX[date]; key = f"{ctx['market_base']}·{ctx['sentiment']}"
        eff = ctx["eff_line_current"]
        if weak_climax_line is not None and key == "弱势·高潮":
            eff = weak_climax_line
        top = gp[gp.final_score >= eff].sort_values("final_score", ascending=False).head(TOP_N)
        rows.append({"signal_date": date, "basket": top.net.mean() if len(top) else 0.0,
                     "n": len(top), "key": key})
    return pd.DataFrame(rows).sort_values("signal_date")


def combo_day_breakdown():
    d = daily_baskets(None)
    print("===== 日度 cohort 按 combo 分解（现行 P0）=====")
    agg = d.groupby("key").agg(days=("basket","size"), ticket_days=("n",lambda s:(s>0).sum()),
                               mean=("basket","mean"), wr=("basket",lambda s:(s>0).mean()))
    agg = agg.sort_values("mean")
    for k, r in agg.iterrows():
        print(f"  {k:9s} days{int(r.days):>3} 出票{int(r.ticket_days):>3} 均净{r['mean']:+.4f} 盈天{r['wr']*100:4.1f}%")
    wc = d[d.key == "弱势·高潮"]
    print(f"\n弱势·高潮 日 basket 明细（n days={len(wc)}）: 均{wc.basket.mean():+.3f} 盈天{(wc.basket>0).mean()*100:.1f}%")
    print("  按月:", wc.assign(m=wc.signal_date.str[:7]).groupby("m").agg(d=("basket","size"),mean=("basket","mean")).round(3).to_dict("index"))
    print("  最大单日贡献:", wc.basket.min(), wc.basket.max(), " | 非零日:", (wc.n>0).sum())


def counterfactual():
    pol = {"P0_current": daily_baskets(None),
           "weakClimax_79": daily_baskets(79.0),
           "weakClimax_85": daily_baskets(85.0)}
    out = {}
    print("\n===== 反事实组合对照（日度 top6 等权，无票=现金）=====")
    for name, d in pol.items():
        out[name] = {}
        for w, dd in [("train", d[d.signal_date<=SPLIT]), ("oos", d[d.signal_date>SPLIT]), ("all", d)]:
            m = _metric_summary(dd.basket)
            m["ticket_days"] = int((dd.n>0).sum()); m["zero_pct"] = round((dd.n==0).mean()*100,1)
            out[name][w] = m
        a = out[name]
        print(f"[{name:14s}] "
              f"TR avg{a['train']['avg_cohort_return_pct']:+.4f} cum{a['train']['cumulative_pct']:+.2f} MDD{a['train']['max_drawdown_pct']:6.2f} | "
              f"OOS avg{a['oos']['avg_cohort_return_pct']:+.4f} cum{a['oos']['cumulative_pct']:+.2f} MDD{a['oos']['max_drawdown_pct']:6.2f} 出票{a['oos']['ticket_days']} | "
              f"ALL cum{a['all']['cumulative_pct']:+.2f} MDD{a['all']['max_drawdown_pct']:6.2f}")
    # 被拦日机会成本
    base = pol["P0_current"]; chg = pol["weakClimax_85"]
    blocked_days = base[(base.key=="弱势·高潮") & (base.n>0)]
    print(f"\n被 85 线拦下的弱势·高潮出票日 n={len(blocked_days)}，这些日 P0 basket 均净={blocked_days.basket.mean():+.4f}（应<0=避损），"
          f"盈天{(blocked_days.basket>0).mean()*100:.1f}%，合计{blocked_days.basket.sum():+.3f}")
    return out


def three_blocks():
    d = daily_baskets(None)
    wc = d[d.key=="弱势·高潮"].sort_values("signal_date")
    print("\n===== 弱势·高潮 三段时间稳健性 =====")
    for i, idx in enumerate(np.array_split(np.arange(len(d.sort_values('signal_date'))),3),1):
        bl = d.sort_values("signal_date").iloc[idx]
        w = bl[bl.key=="弱势·高潮"]
        if len(w): print(f"  block{i} {bl.signal_date.iloc[0]}..{bl.signal_date.iloc[-1]}: 弱势·高潮 days{len(w)} 均{w.basket.mean():+.3f} 盈天{(w.basket>0).mean()*100:.0f}%")


if __name__ == "__main__":
    combo_day_breakdown()
    out = counterfactual()
    three_blocks()
    (OUT/"weak_climax_fix_validation.json").write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str))
    print("\nsaved weak_climax_fix_validation.json")
