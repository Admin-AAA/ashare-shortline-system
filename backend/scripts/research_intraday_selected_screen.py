"""选中池分时特征双窗口筛选（只读，探索性）。

对每个分时特征：日度横截面 Spearman IC（按日算再对天取均值/t），三分位净收益与
高-低价差，月度 IC 同号率；要求冻结/前瞻两窗口 IC 同向、三分位价差同向，
并做 12 特征 Bonferroni。另先校验冻结窗匹配/未匹配票基线，排查覆盖偏差。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
from scipy import stats
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
OUT = B / "output" / "research"
FEATS = ["gap_pct", "am_ret", "pm_ret", "am_minus_pm", "amplitude", "rvol",
         "intraday_mdd", "am_volshare", "vwap_above_volshare", "close_off_high",
         "last30_ret", "vp_corr"]
LABEL = {
    "gap_pct": "隔夜跳空%", "am_ret": "上午收益%", "pm_ret": "下午收益%",
    "am_minus_pm": "上午-下午%", "amplitude": "日内振幅%", "rvol": "bar波动",
    "intraday_mdd": "日内最大回撤%", "am_volshare": "上午量占比",
    "vwap_above_volshare": "VWAP上方量占比", "close_off_high": "收盘离高点%",
    "last30_ret": "尾盘30分收益%", "vp_corr": "量价相关"}


def daily_ic(df, feat):
    ics = []
    for dt, g in df.groupby("dt"):
        x, y = g[feat].values, g.net.values
        m = np.isfinite(x) & np.isfinite(y)
        if m.sum() >= 4 and np.std(x[m]) > 0:
            ics.append((str(dt)[:7], stats.spearmanr(x[m], y[m]).statistic))
    if not ics: return None
    arr = np.array([i[1] for i in ics])
    t = arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr))) if len(arr) > 1 else np.nan
    months = pd.DataFrame(ics, columns=["m", "ic"]).groupby("m").ic.mean()
    sign_frac = max((months > 0).mean(), (months < 0).mean())
    return dict(n_days=len(arr), ic=arr.mean(), t=t, month_sign=sign_frac, n_months=len(months))


def terciles(df, feat):
    x = df[[feat, "net"]].dropna()
    if len(x) < 15 or x[feat].std() == 0: return None
    try:
        q = pd.qcut(x[feat], 3, labels=["lo", "mid", "hi"], duplicates="drop")
    except Exception:
        return None
    g = x.groupby(q, observed=True).net.agg(["mean", "count"])
    if len(g) < 3: return None
    return g.loc["lo", "mean"], g.loc["mid", "mean"], g.loc["hi", "mean"], g.loc["hi", "mean"] - g.loc["lo", "mean"]


def main():
    p = pd.read_pickle(OUT / "intraday_selected_panel.pkl")
    fz, fw = p[p.win == "frozen"].copy(), p[p.win == "forward"].copy()
    # 覆盖偏差：冻结匹配 vs 未匹配
    sel = pd.read_pickle(OUT / "canon_frozen_v2_selected.pkl"); sel["dt"] = sel.dt.astype(str)
    matched = set(map(tuple, fz[["dt", "code"]].values))
    sel["m"] = [ (r.dt, r.code) in matched for r in sel.itertuples()]
    print("=== 冻结覆盖偏差（matched vs missing 净收益）===")
    for m, sub in sel.groupby("m"):
        print(f"  matched={m} n={len(sub)} net均{sub.net.mean():+.4f} win{(sub.net>0).mean():.3f}")
    print(f"\n=== 双窗口特征筛选（frozen n={len(fz)}, forward n={len(fw)}）===")
    rows = []
    for feat in FEATS:
        iz, iw = daily_ic(fz, feat), daily_ic(fw, feat)
        tz, tw = terciles(fz, feat), terciles(fw, feat)
        if not iz or not iw: continue
        same_ic = np.sign(iz["ic"]) == np.sign(iw["ic"]) and np.sign(iz["ic"]) != 0
        spread_same = tz and tw and np.sign(tz[3]) == np.sign(tw[3])
        rows.append((feat, iz, iw, tz, tw, same_ic, spread_same))
    print(f"{'feature':22s} | FROZEN ic/t/月同号/三分位(lo,hi,Δ) | FORWARD ic/t/月同号/三分位(lo,hi,Δ) | IC同向 价差同向")
    for feat, iz, iw, tz, tw, same, ss in rows:
        tzs = f"{tz[0]:+.2f}/{tz[2]:+.2f}/{tz[3]:+.2f}" if tz else "  n/a "
        tws = f"{tw[0]:+.2f}/{tw[2]:+.2f}/{tw[3]:+.2f}" if tw else "  n/a "
        print(f"{LABEL[feat]:11s}{feat:11s} | {iz['ic']:+.4f}/{iz['t']:+5.2f}/{iz['month_sign']:.2f} {tzs:18s} | "
              f"{iw['ic']:+.4f}/{iw['t']:+5.2f}/{iw['month_sign']:.2f} {tws:18s} | {'✓' if same else '×'}    {'✓' if ss else '×'}")
    # Bonferroni 阈值：|t|> z(1-0.025/12)≈2.94（日度 IC t，双尾）
    print("\nBonferroni(12) 日度IC |t| 阈值≈2.94；候选需：两窗IC同向且价差同向、两窗|t|或月同号达标")
    cand = [r for r in rows if r[5] and r[6]]
    print("同向候选：", [(r[0], round(r[1]['ic'], 4), round(r[2]['ic'], 4)) for r in cand] or "无")


if __name__ == "__main__":
    main()
