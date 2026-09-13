"""beta 族 vs ivol20 的正交增量对照：下行beta / 上行beta / 全窗beta / beta不对称。
判据 = 对 ivol20 逐日横截面正交化后，残差 top20% 的「5日内<=-5% 大亏率」抬升（因果出场代理）。
另附 beta_dn20 与 beta20 的横截面相关，判断"下行"是否特殊。
只读。
"""
from __future__ import annotations
import sys, json, warnings
from pathlib import Path
import numpy as np, pandas as pd

warnings.filterwarnings("ignore")
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
OUT = B / "output" / "research"
WINDOWS = {"W1_frozen": ("2025-01-15", "2026-01-16"),
           "W2_forward": ("2026-02-09", "2026-08-05"),
           "W3_third": ("2026-08-07", "2026-09-02")}
REGIMES = ["强牛", "偏多", "震荡", "弱势"]
BETAS = ["beta_dn20", "beta_up20", "beta20", "beta_asym20"]


def _ortho_top_bad(t, col, ctrl="ivol20", thr=0.8):
    def _resid(gp):
        x = gp[ctrl].values.astype(float); y = gp[col].values.astype(float)
        xm, ym = x.mean(), y.mean(); vx = ((x - xm) ** 2).mean()
        if vx <= 1e-18 or len(gp) < 10:
            return pd.Series(np.nan, index=gp.index)
        bb = ((x - xm) * (y - ym)).mean() / vx
        return pd.Series(y - (ym + bb * (x - xm)), index=gp.index)
    t = t.assign(ores=t.groupby("trade_date", group_keys=False).apply(_resid)).dropna(subset=["ores"])
    if len(t) < 5000:
        return None
    t = t.assign(pct=t.groupby("trade_date").ores.rank(pct=True))
    top = t[t.pct >= thr]; rest = t[t.pct < thr]
    return dict(d_bad=float((top.fwd5 <= -5).mean() - (rest.fwd5 <= -5).mean()) * 100,
                d_mean=float(top.fwd5.mean() - rest.fwd5.mean()),
                n=len(t))


def main():
    df = pd.read_pickle(OUT / "resid_downside_panel.pkl")
    lab = json.loads((OUT / "resid_downside_regime.json").read_text())
    df["regime"] = df.trade_date.map(lab)
    print("### 正交于 ivol20 的残差 top20%：dBigLoss(pp) / dMean(%) ###")
    print(f"{'factor':<14}{'window':<12}{'regime':<8}{'dBigLoss':>10}{'dMean':>9}{'n':>10}")
    rows = []
    for w, (a, b) in WINDOWS.items():
        sub = df[(df.trade_date >= a) & (df.trade_date <= b)]
        for reg in REGIMES:
            s = sub[sub.regime == reg][["trade_date", "fwd5", "ivol20"] + BETAS].dropna(subset=["fwd5", "ivol20"])
            if len(s) < 5000:
                continue
            for col in BETAS:
                t = s[["trade_date", "fwd5", "ivol20", col]].dropna()
                r = _ortho_top_bad(t, col)
                if r is None:
                    continue
                rows.append(dict(w=w, reg=reg, col=col, **r))
                print(f"{col:<14}{w.replace('_',' '):<12}{reg:<8}{r['d_bad']:>+10.2f}{r['d_mean']:>+9.3f}{r['n']:>10,}")
    r = pd.DataFrame(rows)
    print("\n### 汇总：各 beta 变体 12 个 (窗×regime) 单元的 dBigLoss 符号 ###")
    for col in BETAS:
        x = r[r.col == col]
        print(f"  {col:<14} 同向(>0) {int((x.d_bad>0).sum())}/{len(x)}  均值{x.d_bad.mean():+.2f}pp  最差{x.d_bad.min():+.2f}  最好{x.d_bad.max():+.2f}")
    print("\n### beta_dn20 vs beta20 vs beta_up20 逐日横截面 Spearman（中位数） ###")
    for w, (a, b) in WINDOWS.items():
        sub = df[(df.trade_date >= a) & (df.trade_date <= b) & (df.regime == "强牛")][["trade_date", "beta_dn20", "beta20", "beta_up20", "ivol20"]].dropna()
        for c1, c2 in (("beta_dn20", "beta20"), ("beta_dn20", "beta_up20")):
            v = sub.groupby("trade_date").apply(lambda g: g[c1].corr(g[c2], method="spearman")).median()
            print(f"  {w} {c1} vs {c2}: rho={v:+.3f}")
    r.to_csv(OUT / "beta_family_ortho_ivol.csv", index=False)
    print("\nsaved output/research/beta_family_ortho_ivol.csv")


if __name__ == "__main__":
    main()
