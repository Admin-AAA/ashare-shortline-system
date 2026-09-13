"""本轮预筛（尾部导向）：不再用「逐月均值同号」，改用「大亏率尾部单调」三窗一致性 + regime 条件化。

输出每个因子的：
- 全样本、每个窗口：日截面 Spearman IC、五分位 fwd5 均值、五分位 大亏率(fwd5<=-5)；
- 强牛子样本：同样指标（L-093 的 ivol20 edge 只在强牛）；
- 一致性判定：`tail_pos`（大亏率 Q5-Q1 三窗同号）与 `bull_tail_pos`（强牛内同号）。
只把通过者升级到生产同构 canonical 三窗终审。
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

OUT = B / "output" / "research"
PANEL = OUT / "si_rf_panel.pkl"
WINDOWS = {"W1": ("2025-01-15", "2026-01-16"),
           "W2": ("2026-02-09", "2026-08-05"),
           "W3": ("2026-08-07", "2026-09-02")}
HORIZON = "fwd5"
LOSS_THR = -5.0

FACTORS = [
    # A 隔夜/日内分解族
    "on_mean5", "on_mean10", "on_mean20", "id_mean5", "id_mean10", "id_mean20",
    "on_minus_id5", "on_minus_id10", "on_minus_id20", "on_share5", "on_share10", "on_share20",
    "on_vol20", "id_vol20", "on_id_vol_ratio20", "on_skew20", "on_negfreq20",
    # B 非对称/特质崩盘风险族
    "vol20_rf", "ddev20", "ddev_ratio20", "dvol20", "dvol_ratio20", "skew20", "kurt20",
    "max1_20", "min1_20", "range_maxmin20", "beta20", "ivol20_rf",
    "rskew20", "rmax1_20", "rmin1_20", "rddev20", "rddev_ratio20", "dbeta20", "dbeta_ratio20",
    # C 存量指标残列
    "asi", "asit", "bbi", "bbi_rel", "brar_ar", "brar_br", "dpo", "ema_12", "ema_50",
    "ema12_rel", "ema50_rel", "ma_volume_5", "ma_volume_10", "mv5_rel", "mtm", "obv",
    "obv_slope20", "psy", "trix", "vr", "wr_6", "wr_10", "kdj_k", "kdj_d", "kdj_j",
    "macd_dif", "macd_dea", "macd_bar", "cci", "boll_pos", "rsi_6", "rsi_12", "rsi_24",
    "bias_5", "bias_10", "bias_20", "max_dd20", "max_dd60", "breakout20", "breakout60",
    "atr_n", "chip_cost_15", "chip_cost_50", "chip_cost_85", "chip_weight_avg",
    "chip_winner_rate", "chip_spread", "chip_pos", "upper_shadow_count20", "holder_pct",
    "profit_efficiency", "vol_ratio_5", "vol_ratio_20", "turnover_rate",
]


def regime_map(dates) -> dict:
    from scripts.research_forward_validation import build_market_snapshots
    from app.engine.aggregator import _market_base
    snaps = build_market_snapshots(sorted(set(dates)))
    return {d: _market_base(s) for d, s in snaps.items()}


def qcuts(d: pd.DataFrame, col: str) -> pd.DataFrame:
    rk = d.groupby("trade_date")[col].rank(method="first")
    cnt = d.groupby("trade_date")[col].transform("size")
    d = d.assign(q=np.clip(((rk - 1) // (cnt / 5)).astype(int), 0, 4))
    return d


def window_stats(d: pd.DataFrame, col: str) -> dict | None:
    d = d[[col, "trade_date", HORIZON]].dropna()
    if len(d) < 3000 or d.trade_date.nunique() < 12 or d[col].nunique() < 10:
        return None
    d = qcuts(d, col)
    g = d.groupby(["trade_date", "q"])
    mean_q = g[HORIZON].mean().unstack("q").mean()
    loss_q = d.assign(bad=(d[HORIZON] <= LOSS_THR).astype(float)).groupby(["trade_date", "q"])["bad"].mean().unstack("q").mean()
    # 日截面 Spearman
    rc = d.groupby("trade_date")[col].rank()
    rh = d.groupby("trade_date")[HORIZON].rank()
    t = pd.DataFrame({"d": d.trade_date.values, "rc": rc.values, "rh": rh.values})
    tg = t.groupby("d")
    ic = ((tg.apply(lambda x: ((x.rc - x.rc.mean()) * (x.rh - x.rh.mean())).mean())) /
          (tg.rc.std() * tg.rh.std())).dropna().mean()
    return {"n": int(len(d)), "days": int(d.trade_date.nunique()),
            "mean": [float(mean_q.get(q, np.nan)) for q in range(5)],
            "loss": [float(loss_q.get(q, np.nan)) for q in range(5)],
            "ic": float(ic),
            "tail_dloss": float(loss_q.get(4, np.nan) - loss_q.get(0, np.nan)),
            "spread": float(mean_q.get(4, np.nan) - mean_q.get(0, np.nan))}


def main():
    df = pd.read_pickle(PANEL)
    df["trade_date"] = df.trade_date.astype(str)
    print(f"panel rows={len(df):,} dates {df.trade_date.min()}..{df.trade_date.max()}", flush=True)
    reg = regime_map(df.trade_date.unique())
    df["regime"] = df.trade_date.map(reg).fillna("?")
    print("regime dist:", df[["trade_date", "regime"]].drop_duplicates().regime.value_counts().to_dict(), flush=True)

    res = {}
    for f in FACTORS:
        if f not in df.columns:
            continue
        rec = {"all": {}, "bull": {}}
        for wn, (a, b) in WINDOWS.items():
            sub = df[(df.trade_date >= a) & (df.trade_date <= b)]
            rec["all"][wn] = window_stats(sub, f)
            bull = sub[sub.regime == "强牛"]
            rec["bull"][wn] = window_stats(bull, f) if len(bull) > 20000 else None
        res[f] = rec

    (OUT / "si_rf_screen.json").write_text(json.dumps(
        res, ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))

    def fmt(v):
        return f"{v:+.3f}" if isinstance(v, float) and np.isfinite(v) else "   -  "

    print("\n===== 全样本：大亏率 O5-O1（三窗同号=★） =====")
    print(f"{'factor':22s} {'W1':>8}{'W2':>8}{'W3':>8}  {'IC均':>7}  {'Δloss同号':>9}")
    rows = []
    for f, rec in res.items():
        tl = [rec["all"][w]["tail_dloss"] if rec["all"][w] else np.nan for w in WINDOWS]
        ics = [rec["all"][w]["ic"] for w in WINDOWS if rec["all"][w]]
        if all(np.isfinite(x) for x in tl):
            same = len({np.sign(x) for x in tl if abs(x) > 1e-9}) == 1
        else:
            same = False
        rows.append((f, tl, np.mean(ics) if ics else np.nan, same))
    for f, tl, ic, same in sorted(rows, key=lambda r: -abs(r[1][0] if np.isfinite(r[1][0]) else 0)):
        print(f"{f:22s} {fmt(tl[0])} {fmt(tl[1])} {fmt(tl[2])}  {ic:+7.4f}  {'★' if same else ''}")

    print("\n===== 强牛子样本：大亏率 O5-O1（三窗同号=★） =====")
    print(f"{'factor':22s} {'W1':>8}{'W2':>8}{'W3':>8}  {'IC均':>7}  {'Δloss同号':>9}")
    rows2 = []
    for f, rec in res.items():
        tl = [rec["bull"][w]["tail_dloss"] if rec["bull"][w] else np.nan for w in WINDOWS]
        ics = [rec["bull"][w]["ic"] for w in WINDOWS if rec["bull"][w]]
        if all(np.isfinite(x) for x in tl):
            same = len({np.sign(x) for x in tl if abs(x) > 1e-9}) == 1
        else:
            same = False
        rows2.append((f, tl, np.mean(ics) if ics else np.nan, same))
    for f, tl, ic, same in sorted(rows2, key=lambda r: -abs(r[1][0] if np.isfinite(r[1][0]) else 0)):
        print(f"{f:22s} {fmt(tl[0])} {fmt(tl[1])} {fmt(tl[2])}  {ic:+7.4f}  {'★' if same else ''}")
    print("\nsaved output/research/si_rf_screen.json")


if __name__ == "__main__":
    main()
