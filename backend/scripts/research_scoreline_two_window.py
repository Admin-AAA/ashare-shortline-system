"""跨两窗口（冻结244d + 前瞻117d，共361d）稳健分数线搜索（只读）。

教训 L-058：单窗口最优=过拟合。冻结窗口里 P0 优于统一72，前瞻窗口里 P0 反劣于统一72。
本脚本统一两窗口的候选（当前口径 regime 标签 + 因果净收益 + final/score），对一组
原则性候选分数线策略，同时在两个窗口评估日度 top6 cohort（无票=现金），只接受
「两窗口都不劣于 P0、且逐月为正占比不降」的 Pareto 改进。
"""
from __future__ import annotations
import sys, pickle, json
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_intraday_portfolio_levers import _metric_summary, TOP_N  # type: ignore
from scripts.research_forward_validation import build_forward, build_market_snapshots  # type: ignore
from app.engine.aggregator import _market_base, _market_sentiment  # noqa

OUT = Path(__file__).resolve().parents[1] / "output" / "research"


def frozen_panel():
    tr = pd.read_pickle(OUT / "regime_scoreline_trades.pkl")
    d = tr.rename(columns={"final_score": "score"})[["signal_date", "code", "score", "net", "market_base", "sentiment"]]
    d["window"] = "frozen"
    return d.rename(columns={"market_base": "base", "sentiment": "sent"})


def forward_panel():
    tr = build_forward()
    dates = sorted(tr.signal_date.unique())
    snaps = build_market_snapshots(dates)
    tr = tr[tr.signal_date.isin(snaps)].copy()
    tr["base"] = tr.signal_date.map(lambda d: _market_base(snaps.get(d)))
    tr["sent"] = tr.signal_date.map(lambda d: _market_sentiment(snaps.get(d)))
    tr["window"] = "forward"
    return tr[["signal_date", "code", "score", "net", "base", "sent", "window"]]


def daily(df, line_map):
    rows = []
    df = df.assign(key=df.base + "·" + df.sent)
    for (date, win), gp in df.groupby(["signal_date", "window"]):
        eff = gp.key.map(line_map).fillna(72.0)
        g = gp.assign(eff=eff)
        top = g[g.score >= g.eff].sort_values("score", ascending=False).head(TOP_N)
        rows.append({"signal_date": date, "window": win,
                     "basket": top.net.mean() if len(top) else 0.0, "n": len(top)})
    return pd.DataFrame(rows)


def metrics(d):
    m = _metric_summary(d.basket)
    m["ticket"] = int((d.n > 0).sum()); m["days"] = len(d)
    return m


# P0 现行有效线（resolve_score_line_context 对 base=72 的产出，已核对）
P0 = {"强牛·高潮": 64, "强牛·活跃": 64, "强牛·平淡": 78, "强牛·冰点": 78,
      "偏多·高潮": 85, "偏多·活跃": 71.4, "偏多·平淡": 72, "偏多·冰点": 72,
      "震荡·高潮": 85, "震荡·活跃": 72, "震荡·平淡": 72, "震荡·冰点": 72,
      "弱势·高潮": 60, "弱势·活跃": 60, "弱势·平淡": 60, "弱势·冰点": 60}

VARIANTS = {
    "V0_P0": P0,
    "V1_wc85": {**P0, "弱势·高潮": 85},
    "V2_duo_active72": {**P0, "弱势·高潮": 85, "偏多·活跃": 72},
    "V3_weak_active68": {**P0, "弱势·高潮": 85, "偏多·活跃": 72, "弱势·活跃": 68},
    "V4_weak_active72": {**P0, "弱势·高潮": 85, "偏多·活跃": 72, "弱势·活跃": 72, "弱势·平淡": 72, "弱势·冰点": 72},
    "V5_tight_duo_climax": {**P0, "弱势·高潮": 85, "偏多·活跃": 74, "弱势·活跃": 70},
    "V6_uniform72": {k: 72 for k in P0},
}


def main():
    fz = frozen_panel(); fw = forward_panel()
    alltr = pd.concat([fz, fw], ignore_index=True)
    print(f"unified: frozen {fz.signal_date.nunique()}d/{len(fz)}  forward {fw.signal_date.nunique()}d/{len(fw)}")
    summary = {}
    for name, lm in VARIANTS.items():
        d = daily(alltr, lm)
        line = f"[{name:18s}]"
        rec = {}
        for w in ["frozen", "forward"]:
            dd = d[d.window == w]; m = metrics(dd)
            mon = dd.assign(m=dd.signal_date.str[:7]).groupby("m").basket.mean()
            rec[w] = {"cum": round(m["cumulative_pct"], 2), "avg": round(m["avg_cohort_return_pct"], 4),
                      "mdd": round(m["max_drawdown_pct"], 2), "posMon": round((mon > 0).mean(), 2),
                      "ticket": m["ticket"], "days": m["days"]}
            line += f" | {w[:3]} cum{m['cumulative_pct']:+7.2f} MDD{m['max_drawdown_pct']:6.2f} 月正{(mon>0).mean():.2f} 出票{m['ticket']:>3}"
        summary[name] = rec
        print(line)
    (OUT / "scoreline_two_window.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    # 逐格边际：两窗口里把某 combo 线从 P0 值改到候选值，该 combo 日 cohort 的变化（定位稳健方向）
    print("\n===== 各 combo 在两窗口的日 cohort 均净（按不同固定线）=====")
    alltr = alltr.assign(key=alltr.base + "·" + alltr.sent)
    grid = {}
    for key in ["强牛·高潮", "强牛·活跃", "偏多·高潮", "偏多·活跃", "震荡·高潮", "震荡·活跃",
                "弱势·高潮", "弱势·活跃", "弱势·平淡"]:
        sub = alltr[alltr.key == key]
        if sub.empty: continue
        row = f"  {key:9s}"
        grid[key] = {}
        for L in [60, 64, 68, 72, 76, 82, 85]:
            cells = []
            for w in ["frozen", "forward"]:
                s = sub[sub.window == w]
                vals = []
                for date, gp in s.groupby("signal_date"):
                    top = gp[gp.score >= L].sort_values("score", ascending=False).head(TOP_N)
                    vals.append(top.net.mean() if len(top) else 0.0)
                cells.append(np.mean(vals) if vals else np.nan)
            grid[key][L] = [round(cells[0], 3), round(cells[1], 3)]
            row += f" >={L}:{cells[0]:+.2f}/{cells[1]:+.2f}"
        print(row)
    (OUT / "scoreline_combo_grid.json").write_text(json.dumps(grid, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
