"""真·前瞻窗口(2026-02-09~08-06, L-060从未用过)验证 弱势·高潮 否决。

前向交易来自 DB canonical run btr_fa625（真实DailyKbar、因果出场正确，score>=70）。
按当前生产定义重建每日 regime/情绪，日度 top6 等权 cohort（无票=现金），对照：
  P0   现行（弱势各情绪线=60，弱市高潮被放行）
  VETO 弱势·高潮=999、弱势·平淡=999（保留其余弱市情绪）
只读。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from scripts.research_forward_validation import build_forward, build_market_snapshots  # noqa
from app.engine.aggregator import _market_base, _market_sentiment  # noqa

TOPN = 6
P0 = {"强牛·高潮": 64, "强牛·活跃": 64, "强牛·平淡": 78, "强牛·冰点": 78,
      "偏多·高潮": 85, "偏多·活跃": 71.4, "偏多·平淡": 72, "偏多·冰点": 72,
      "震荡·高潮": 85, "震荡·活跃": 72, "震荡·平淡": 72, "震荡·冰点": 72,
      "弱势·高潮": 60, "弱势·活跃": 60, "弱势·平淡": 60, "弱势·冰点": 60}
VETO = {**P0, "弱势·高潮": 999.0}


def daily(df, lm):
    rows = []
    for date, gp in df.groupby("signal_date"):
        key = f"{gp.base.iloc[0]}·{gp.sent.iloc[0]}"
        eff = lm.get(key, 72.0)
        top = gp[gp.score >= eff].sort_values("score", ascending=False).head(TOPN)
        rows.append({"date": date, "key": key, "b": top.net.mean() if len(top) else 0.0, "n": len(top)})
    return pd.DataFrame(rows)


def summ(d):
    return (f"日均{d.b.mean():+.4f}% 累计{d.b.sum():+7.2f} 盈天{(d.b>0).mean()*100:4.1f}% "
            f"出票天{(d.n>0).sum()}/{len(d)}")


def main():
    tr = build_forward()
    dates = sorted(tr.signal_date.unique())
    snaps = build_market_snapshots(dates)
    tr = tr[tr.signal_date.isin(snaps)].copy()
    tr["base"] = tr.signal_date.map(lambda d: _market_base(snaps.get(d)))
    tr["sent"] = tr.signal_date.map(lambda d: _market_sentiment(snaps.get(d)))
    tr["key"] = tr.base + "·" + tr.sent
    print(f"forward 窗口 {dates[0]}~{dates[-1]}  {tr.signal_date.nunique()}天 {len(tr)}候选(score>=70)")

    # 弱市各情绪格的逐笔净收益（前瞻窗口真实因果）
    print("\n=== 前瞻窗口 弱势各情绪格（候选逐笔）===")
    weak = tr[tr.base == "弱势"]
    print(weak.groupby("sent").agg(n=("net", "size"), avg=("net", "mean"),
                                   wr=("net", lambda x: (x > 0).mean() * 100)).round(3).to_string())

    a, b = daily(tr, P0), daily(tr, VETO)
    print("\n=== 日度top6组合（无票=现金）===")
    print("P0  :", summ(a))
    print("VETO:", summ(b))
    diff = (b.set_index("date").b - a.set_index("date").b)
    chg = diff[diff.abs() > 1e-9]
    print(f"\n变化日 {len(chg)}: VETO更优 {(chg>0).sum()} / 更差 {(chg<0).sum()}, 日均Δ {chg.mean():+.4f}%")
    # 逐月
    m = pd.DataFrame({"P0": a.set_index("date").b, "VETO": b.set_index("date").b})
    mg = m.groupby(m.index.str[:7]).mean()
    mg["delta"] = (mg.VETO - mg.P0).round(3)
    print("\n逐月:\n", mg.round(3).to_string())


if __name__ == "__main__":
    main()
