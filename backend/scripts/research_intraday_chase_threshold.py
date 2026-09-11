"""pm_ret（信号日下午追高）固定阈值 + 分regime + 控制当日涨幅增量检验（只读探索）。"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
OUT = B / "output" / "research"


def boot_ci(diff, draws=5000, seed=7):
    rng = np.random.default_rng(seed); d = np.asarray(diff); n = len(d)
    if n < 3: return (np.nan, np.nan)
    return np.percentile([d[rng.integers(0, n, n)].mean() for _ in range(draws)], [2.5, 97.5])


def add_daily_pct():
    env = (B / ".env").read_text()
    url = [l.split("=", 1)[1].strip().strip('"').strip("'") for l in env.splitlines()
           if l.startswith("ASHARE_DATABASE_URL=")][0]
    eng = create_engine(url)
    p = pd.read_pickle(OUT / "intraday_selected_panel.pkl")
    pairs = p[["dt", "code"]].drop_duplicates()
    d0, d1 = pairs.dt.min(), pairs.dt.max()
    codes = sorted(pairs.code.unique())
    sql = text("""SELECT trade_date dt, code, pct_chg, `close` FROM daily_kbars
                  WHERE trade_date BETWEEN :d0 AND :d1 AND code IN :c""")
    dk = pd.read_sql(sql, eng, params={"d0": d0, "d1": d1, "c": tuple(codes)})
    dk["dt"] = dk.dt.astype(str); dk["code"] = dk.code.astype(str)
    return p.merge(dk[["dt", "code", "pct_chg"]], on=["dt", "code"], how="left")


def veto(d, feat, cond_keep, all_dates):
    x = d.dropna(subset=[feat]).copy()
    keep = x[cond_keep(x)]
    b = x.groupby("dt").net.mean().reindex(all_dates).fillna(0)
    n = keep.groupby("dt").net.mean().reindex(all_dates).fillna(0)
    cut = x[~cond_keep(x)]
    touched = sorted(set(cut.dt) & set(x.dt))
    chg = (n - b).loc[touched]
    lo, hi = boot_ci(chg.values)
    md = (n - b); md.index = md.index.astype(str).str[:7]
    ms = md.groupby(level=0).sum(); msign = max((ms > 0).mean(), (ms < 0).mean())
    return dict(ncut=len(cut), cut_mean=cut.net.mean(), cut_win=(cut.net > 0).mean(),
                b=b.mean(), n=n.mean(), nday=len(chg), lo=lo, hi=hi, msign=msign)


def main():
    p = add_daily_pct()
    p["base"] = p.key.str.split("·").str[0]
    fz, fw = p[p.win == "frozen"].copy(), p[p.win == "forward"].copy()
    fzd = sorted(pd.read_pickle(OUT / "canon_frozen_v2_selected.pkl").dt.astype(str).unique())
    fwd_d = sorted(pd.read_pickle(OUT / "canon_forward_v2_selected.pkl").dt.astype(str).unique())

    print("=== pm_ret 分位数（看固定阈值落在哪）===")
    for name, d in [("frozen", fz), ("forward", fw)]:
        print(f" {name}:", {q: round(d.pm_ret.quantile(q), 2) for q in (.5,.7,.8,.9,.95)})

    print("\n=== 固定阈值否决 pm_ret > T（保留 pm_ret<=T）===")
    for T in (2.0, 3.0, 4.0, 5.0):
        for name, d, ad in [("frozen", fz, fzd), ("forward", fw, fwd_d)]:
            r = veto(d, "pm_ret", lambda x, T=T: x.pm_ret <= T, ad)
            print(f"  T={T} {name:7s} 砍{r['ncut']:>3}均{r['cut_mean']:+.3f}/w{r['cut_win']:.2f} "
                  f"日{r['b']:+.4f}->{r['n']:+.4f} 变化日{r['nday']} CI[{r['lo']:+.3f},{r['hi']:+.3f}] 月同号{r['msign']:.2f}")

    print("\n=== pm_ret 顶档(>80分位) 分 regime 格（两窗）===")
    for name, d in [("frozen", fz), ("forward", fw)]:
        thr = d.pm_ret.quantile(0.8)
        print(f" --{name} (thr={thr:.2f})--")
        for key, g in d.groupby("key"):
            hi = g[g.pm_ret >= thr]; lo = g[g.pm_ret < thr]
            if len(hi) >= 5:
                print(f"   {key:9s} 顶档n{len(hi):>3}均{hi.net.mean():+.3f}/w{(hi.net>0).mean():.2f} | 其余n{len(lo)}均{lo.net.mean():+.3f}")

    print("\n=== 控制信号日 pct_chg 后，pm_ret 顶档是否仍更差（双排序）===")
    for name, d in [("frozen", fz), ("forward", fw)]:
        x = d.dropna(subset=["pct_chg", "pm_ret"]).copy()
        x["pc_bin"] = pd.qcut(x.pct_chg, 3, labels=["低涨", "中涨", "高涨"], duplicates="drop")
        x["pm_hi"] = x.pm_ret >= x.pm_ret.quantile(0.8)
        print(f" --{name}--")
        for bn, g in x.groupby("pm_hi", observed=True):
            print(f"   pm_hi={bn} n{len(g)} 均{g.net.mean():+.3f}/w{(g.net>0).mean():.2f}")
        t = x.groupby(["pc_bin", "pm_hi"], observed=True).net.agg(["mean", "count"])
        print(t.to_string())


if __name__ == "__main__":
    main()
