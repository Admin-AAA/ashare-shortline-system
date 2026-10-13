"""用【修复后的真实因果出场】重新标定 regime×情绪×分数线 选股政策。

背景：frozen snapshot 的 outcome_bars 缺 amount，旧研究脚本(_to_bar_obj/_bar_obj)
构造的 bar 被全部判为"卖不出"，因果出场退化为持有到 T+5 收盘（L-060 的阈值即按此
错误口径标定）。本脚本对每个候选同时算两种净收益：
  net_horizon: T+1开 -> 第5日收（复现旧 bug 口径，仅用于对照）
  net_causal : 真实 -4.5止损/+6止盈/profit_floor移动止盈/费用/滑点（生产口径）
然后逐 regime×情绪 单元格、逐候选分数线 {60,64,68,72} 给 训练/OOS 的
日度等权 cohort 均值、胜率、正收益日占比、出票数，找"OOS 仍为正且跨期符号一致"
的稳健阈值。只决定"选哪些/出不出票"，不做任何仓位。只读。
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_intraday_portfolio_levers import (  # type: ignore
    _load_snapshot, _simulate_card, SLIPPAGE_BPS, FEE_PCT,
)

OUT = Path(__file__).resolve().parents[1] / "output" / "research"
LINES = [60.0, 64.0, 68.0, 72.0]
TOP_N = 10
TRAIN_SPLIT = "2025-08-21"


def horizon_net(day, card) -> float | None:
    """复现旧 bug 口径：忽略盘中止损止盈，T+1 开盘买、最后一根 bar 收盘卖。"""
    raw = (day.get("outcome_bars") or {}).get(str(card.get("code"))) or []
    if len(raw) < 2:
        return None
    e = float(raw[0].get("open") or 0)
    if e <= 0:
        return None
    sig = float(card.get("close") or card.get("recommend_price") or 0)
    if sig <= 0 or (e / sig - 1) * 100 > 10:
        return None
    x = float(raw[-1].get("close") or 0)
    if x <= 0:
        return None
    ep = e * (1 + SLIPPAGE_BPS / 1e4)
    return (x * (1 - SLIPPAGE_BPS / 1e4) / ep - 1) * 100 - FEE_PCT


def build():
    days = _load_snapshot()
    rows = []
    for day in days:
        snap = day.get("market_snapshot") or {}
        from app.engine.aggregator import _market_base, _market_sentiment
        mb = _market_base(snap); se = _market_sentiment(snap)
        for card in day.get("cards") or []:
            t = _simulate_card(day, card)
            if t is None:
                continue
            hn = horizon_net(day, card)
            rows.append({
                "signal_date": t.signal_date, "code": t.code, "industry": t.industry,
                "final_score": t.final_score, "mb": mb, "se": se,
                "net_causal": t.net_return_pct, "net_horizon": hn,
            })
    return pd.DataFrame(rows)


def cell_line_table(m: pd.DataFrame, netcol: str) -> list[dict]:
    out = []
    for (mb, se), g in m.groupby(["mb", "se"]):
        for line in LINES:
            sel = g[g.final_score >= line]
            for window, dd in [("train", sel[sel.signal_date <= TRAIN_SPLIT]),
                               ("oos", sel[sel.signal_date > TRAIN_SPLIT]),
                               ("all", sel)]:
                daily = dd.groupby("signal_date")[netcol].mean()
                if len(dd) == 0:
                    continue
                out.append({
                    "cell": f"{mb}·{se}", "line": line, "window": window,
                    "trades": len(dd), "pick_days": len(daily),
                    "trade_avg": round(dd[netcol].mean(), 3),
                    "day_avg": round(daily.mean(), 3),
                    "wr": round((dd[netcol] > 0).mean() * 100, 1),
                    "pos_day": round((daily > 0).mean() * 100, 1),
                })
    return out


def policy_daily(m: pd.DataFrame, line_map: dict, netcol: str, top_n=TOP_N):
    """line_map: {(mb,se): line}; 缺省用 default_line。返回每日 cohort（空仓记0）。"""
    default = line_map.get("__default__", 72.0)
    recs = []
    for (date, mb, se), g in m.groupby(["signal_date", "mb", "se"]):
        line = line_map.get((mb, se), default)
        sel = g[g.final_score >= line].sort_values("final_score", ascending=False).head(top_n)
        recs.append({"date": date, "mb": mb, "se": se,
                     "cohort": sel[netcol].mean() if len(sel) else np.nan,
                     "npick": len(sel)})
    d = pd.DataFrame(recs).set_index("date").sort_index()
    return d


def summarize_policy(d, label):
    rows = {}
    for window, dd in [("all", d), ("train", d[d.index <= TRAIN_SPLIT]), ("oos", d[d.index > TRAIN_SPLIT])]:
        picked = dd[dd.npick > 0]
        flat0 = dd["cohort"].fillna(0)
        rows[window] = {
            "days": int(len(dd)), "pick_days": int(len(picked)),
            "pick_day_pct": round(len(picked) / max(len(dd), 1) * 100, 1),
            "per_pick_day_avg": round(picked["cohort"].mean(), 4),
            "flat0_day_avg": round(flat0.mean(), 4),
            "pos_pick_day": round((picked["cohort"] > 0).mean() * 100, 1),
            "total_picks": int(dd.npick.sum()),
        }
    return {"policy": label, **{f"{k}_{kk}": vv for k, v in rows.items() for kk, vv in v.items()}}


def main():
    cache = OUT / "regime_recalib_trades.pkl"
    if cache.exists():
        m = pd.read_pickle(cache)
    else:
        m = build()
        m.to_pickle(cache)
    print(f"trades={len(m)} dates {m.signal_date.min()}..{m.signal_date.max()}")

    # 1) 两种出场口径对照（全候选 cell 均值）
    print("\n=== 出场口径对照（全候选，cell 因果 vs 持有到期）===")
    cmp = m.groupby(["mb", "se"]).agg(
        n=("net_causal", "size"),
        causal=("net_causal", "mean"), horizon=("net_horizon", "mean")).round(3)
    print(cmp.to_string())

    # 2) 逐 cell×line×window（因果）
    tbl = cell_line_table(m, "net_causal")
    df = pd.DataFrame(tbl)
    print("\n=== 逐 cell×line 因果日度cohort（day_avg / pos_day% / trades）train|oos ===")
    for cell in sorted(df.cell.unique()):
        print(f"\n## {cell}")
        sub = df[df.cell == cell]
        for line in LINES:
            row = sub[sub.line == line].set_index("window")
            try:
                tr = row.loc["train"]; oo = row.loc["oos"]
                print(f"  line>={line:>4.0f}  TRAIN day={tr.day_avg:>6.3f} posday={tr.pos_day:>5.1f}% n={tr.trades:>4} | "
                      f"OOS day={oo.day_avg:>6.3f} posday={oo.pos_day:>5.1f}% n={oo.trades:>4}")
            except KeyError:
                pass

    # 3) 端到端政策对比（因果）
    current = {"__default__": 72.0, ("强牛", "高潮"): 64.0, ("强牛", "活跃"): 64.0,
               ("偏多", "活跃"): 71.4, ("震荡", "活跃"): 72.0}
    # 候选新政策：收紧高潮（只放最高分），活跃中高分放行，弱势只放活跃
    proposed = {"__default__": 72.0, ("强牛", "高潮"): 72.0, ("强牛", "活跃"): 68.0,
                ("偏多", "活跃"): 68.0, ("震荡", "活跃"): 72.0, ("弱势", "活跃"): 68.0,
                ("偏多", "高潮"): 100.0, ("震荡", "高潮"): 100.0, ("弱势", "高潮"): 100.0}
    strict72 = {"__default__": 72.0}
    policies = {"current_L060": current, "proposed": proposed, "strict72": strict72}
    summ = []
    print("\n=== 端到端政策对比（因果，日度cohort，空仓日记0）===")
    for name, lm in policies.items():
        d = policy_daily(m, lm, "net_causal")
        s = summarize_policy(d, name)
        summ.append(s)
    ps = pd.DataFrame(summ).set_index("policy")
    cols = [c for c in ps.columns if "flat0_day_avg" in c or "pick_day_pct" in c or "per_pick_day" in c or "pos_pick_day" in c or "total_picks" in c]
    print(ps[cols].T.to_string())

    (OUT / "regime_recalib.json").write_text(json.dumps(
        {"exit_compare": cmp.reset_index().to_dict("records"),
         "cell_line": tbl, "policy_compare": summ}, ensure_ascii=False, indent=2, default=str))
    print("\nsaved output/research/regime_recalib.json")


if __name__ == "__main__":
    main()
