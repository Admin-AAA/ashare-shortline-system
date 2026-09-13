"""下行族 follow-up：以"因果出场代理统计量"（fwd5<=-5% 大亏率）为主判据的分诊。
1) 按 regime（强牛/偏多/震荡/弱势）逐窗看 ivol20 的十分位大亏率梯度；
2) 对 ivol20 逐日横截面正交化后的残差 top20%，其大亏率是否仍升高（增量检验）；
3) 强牛日 ivol20 完整十分位表（含中位数收益、胜率）。
只读。
"""
from __future__ import annotations
import sys, json, warnings
from pathlib import Path
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))
OUT = B / "output" / "research"
PANEL = OUT / "resid_downside_panel.pkl"
SNAP = OUT / "resid_downside_regime.json"
WINDOWS = {
    "W1_frozen": ("2025-01-15", "2026-01-16"),
    "W2_forward": ("2026-02-09", "2026-08-05"),
    "W3_third": ("2026-08-07", "2026-09-02"),
}
REGIMES = ["强牛", "偏多", "震荡", "弱势"]


def deciles(d, col, horizon="fwd5", nq=10):
    d = d[["trade_date", col, horizon]].dropna()
    if len(d) < 2000:
        return None
    rk = d.groupby("trade_date")[col].rank(method="first")
    cnt = d.groupby("trade_date")[col].transform("size")
    d = d.assign(q=np.clip(((rk - 1) // (cnt / nq)).astype(int), 0, nq - 1))
    return d, d.groupby("q")[horizon].agg(
        mean="mean", bad=lambda s: float((s <= -5.0).mean()), win=lambda s: float((s > 0).mean()))


def main():
    df = pd.read_pickle(PANEL)
    lab = json.loads(SNAP.read_text())
    df["regime"] = df.trade_date.map(lab)

    print("############ A. ivol20 十分位【大亏率%】按 regime（主判据=因果出场代理） ############")
    for col, name in (("ivol20", "ivol20"), ("ivol_dn20", "ivol_dn20"), ("ivol_up20", "ivol_up20")):
        print(f"\n--- {name} ---")
        print(f"{'window|regime':<20}" + "".join(f"{'Q'+str(i+1):>7}" for i in range(10)) + f"{'Q10-Q1':>9}{'n':>12}")
        for wname, (a, b) in WINDOWS.items():
            sub = df[(df.trade_date >= a) & (df.trade_date <= b)]
            for reg in REGIMES:
                s = sub[sub.regime == reg]
                r = deciles(s, col)
                if r is None:
                    continue
                _, g = r
                bad = g["bad"].reindex(range(10))
                print(f"{wname.replace('_',' ')+'|'+reg:<20}"
                      + "".join(f"{bad.get(i, np.nan)*100:>7.1f}" for i in range(10))
                      + f"{(bad.get(9,np.nan)-bad.get(0,np.nan))*100:>+9.1f}{int(g['bad'].sum()*0):>12}"
                      + f"  n={len(r[0]):,}")

    print("\n\n############ B. 增量检验：对 ivol20 逐日正交化后，残差 top20% 的大亏率 ############")
    cols = ["ivol_dn20", "ivol_up20", "ivol_asym20", "worst_resid20", "dn_share20",
            "beta_dn20", "beta_asym20", "rdnvol20", "rdn_ratio20"]
    print(f"{'factor':<16}{'window':<12}{'dBigLoss':>10}{'dMean':>9}{'top_bad%':>9}{'rest_bad%':>10}{'n':>9}")
    for wname, (a, b) in WINDOWS.items():
        sub = df[(df.trade_date >= a) & (df.trade_date <= b)]
        for reg in REGIMES:
            s = sub[sub.regime == reg][["trade_date", "fwd5", "ivol20"] + cols].dropna(subset=["fwd5", "ivol20"])
            if len(s) < 5000:
                continue
            for col in cols:
                t = s[["trade_date", "fwd5", "ivol20", col]].dropna()
                if len(t) < 5000:
                    continue
                # 逐日横截面 OLS 残差
                def _resid(gp):
                    x = gp.ivol20.values.astype(float); y = gp[col].values.astype(float)
                    xm, ym = x.mean(), y.mean()
                    vx = ((x - xm) ** 2).mean()
                    if vx <= 1e-18 or len(gp) < 10:
                        return pd.Series(np.nan, index=gp.index)
                    bb = ((x - xm) * (y - ym)).mean() / vx
                    return pd.Series(y - (ym + bb * (x - xm)), index=gp.index)
                t = t.assign(ores=t.groupby("trade_date", group_keys=False).apply(_resid)).dropna(subset=["ores"])
                if len(t) < 5000:
                    continue
                t = t.assign(pct=t.groupby("trade_date").ores.rank(pct=True))
                top = t[t.pct >= 0.8]; rest = t[t.pct < 0.8]
                dbl = float((top.fwd5 <= -5).mean() - (rest.fwd5 <= -5).mean()) * 100
                dm = float(top.fwd5.mean() - rest.fwd5.mean())
                print(f"{col:<16}{wname.replace('_',' ')+'|'+reg:<12}{dbl:>+10.2f}{dm:>+9.3f}"
                      f"{(top.fwd5<=-5).mean()*100:>9.1f}{(rest.fwd5<=-5).mean()*100:>10.1f}{len(t):>9,}")

    print("\n\n############ C. 强牛日 ivol20 完整十分位（mean / 大亏率% / 胜率%） ############")
    for wname, (a, b) in WINDOWS.items():
        s = df[(df.trade_date >= a) & (df.trade_date <= b) & (df.regime == "强牛")]
        r = deciles(s, "ivol20")
        if r is None:
            continue
        _, g = r
        print(f"\n{wname} 强牛 n={len(r[0]):,}")
        for i in range(10):
            if i in g.index:
                print(f"  Q{i+1:<3} mean{g.loc[i,'mean']:>+7.3f}  大亏{g.loc[i,'bad']*100:>5.1f}%  胜率{g.loc[i,'win']*100:>5.1f}%")


if __name__ == "__main__":
    main()
