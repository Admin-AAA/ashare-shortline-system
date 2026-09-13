"""特质风险下行族：三窗口 × 强牛条件化的全市场分诊（只读）。
输出：因子覆盖率/相关性、强牛日内十分位梯度、极端尾部 top20% 对照、以及对 ivol20 的
逐日横截面正交化后残余尾部的增量检验。
用法：python research_resid_downside_screen.py
"""
from __future__ import annotations
import sys, json, itertools, warnings
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
FACTORS = [
    ("ivol20 双侧特质波动(L-093现行)", "ivol20"),
    ("ivol_dn20 残差下行半波动[主假设]", "ivol_dn20"),
    ("ivol_up20 残差上行半波动[对照]", "ivol_up20"),
    ("ivol_asym20 残差左右不对称", "ivol_asym20"),
    ("dn_ratio20 下行占比", "dn_ratio20"),
    ("worst_resid20 尾部深度", "worst_resid20"),
    ("dn_share20 下行日占比", "dn_share20"),
    ("beta_dn20 下行beta", "beta_dn20"),
    ("beta_asym20 beta不对称", "beta_asym20"),
    ("rdnvol20 原始下行波动", "rdnvol20"),
    ("rdn_ratio20 原始下行占比", "rdn_ratio20"),
]


def load():
    df = pd.read_pickle(PANEL)
    lab = json.loads(SNAP.read_text())
    df["regime"] = df.trade_date.map(lab)
    return df


def decile_table(d: pd.DataFrame, col: str, horizon="fwd5", nq=10):
    d = d[["trade_date", col, horizon]].dropna()
    if len(d) < 2000:
        return None
    rk = d.groupby("trade_date")[col].rank(method="first")
    cnt = d.groupby("trade_date")[col].transform("size")
    d = d.assign(q=np.clip(((rk - 1) // (cnt / nq)).astype(int), 0, nq - 1))
    daily = d.groupby(["trade_date", "q"])[horizon].mean().unstack("q")
    qm = daily.mean()
    bad = d.groupby("q")[horizon].apply(lambda s: float((s <= -5.0).mean()))
    top = qm.get(nq - 1, np.nan)
    bot = qm.get(0, np.nan)
    return dict(n=int(len(d)), days=int(d.trade_date.nunique()),
                qmeans=[float(qm.get(q, np.nan)) for q in range(nq)],
                spread=float(top - bot),
                bad=[float(bad.get(q, np.nan)) for q in range(nq)],
                top=float(top), bot=float(bot))


def monthly_sign(spread_series, sgn):
    m = spread_series.groupby(spread_series.index.str[:7]).mean()
    return float((np.sign(m) == sgn).mean()), int(len(m))


def tail_compare(d: pd.DataFrame, col: str, thr=0.80, horizon="fwd5"):
    """当日横截面 pct>=thr 记 top，返回 top/rest 的 fwd 均值与差、逐月同号。"""
    d = d[["trade_date", col, horizon]].dropna().copy()
    if len(d) < 2000:
        return None
    d["pct"] = d.groupby("trade_date")[col].rank(pct=True)
    d["is_top"] = d.pct >= thr
    g = d.groupby(["trade_date", "is_top"])[horizon].mean().unstack("is_top")
    if True not in g.columns or False not in g.columns:
        return None
    dd = (g[True] - g[False]).dropna()
    ss, nm = monthly_sign(dd, np.sign(dd.mean()) if dd.mean() != 0 else 1.0)
    return dict(n_top=int(d.is_top.sum()), mean_top=float(d[d.is_top][horizon].mean()),
                mean_rest=float(d[~d.is_top][horizon].mean()),
                delta=float(dd.mean()), samesign=ss, nmonths=nm,
                bad_top=float((d[d.is_top][horizon] <= -5.0).mean()),
                bad_rest=float((d[~d.is_top][horizon] <= -5.0).mean()))


def ortho_residual_tail(df: pd.DataFrame, col: str, ctrl: str, thr=0.80, horizon="fwd5"):
    """逐日横截面把 col 对 ctrl 回归取残差，再取残差 top20% 看是否仍更差（增量检验）。"""
    d = df[["trade_date", col, ctrl, horizon]].dropna().copy()
    if len(d) < 2000:
        return None
    def _resid(gp):
        x = gp[ctrl].values.astype(float)
        y = gp[col].values.astype(float)
        xm, ym = x.mean(), y.mean()
        vx = ((x - xm) ** 2).mean()
        if vx <= 1e-18 or len(gp) < 10:
            return pd.Series(np.nan, index=gp.index)
        b = ((x - xm) * (y - ym)).mean() / vx
        return pd.Series(y - (ym + b * (x - xm)), index=gp.index)
    d["ores"] = d.groupby("trade_date", group_keys=False).apply(_resid)
    d = d.dropna(subset=["ores"])
    d["pct"] = d.groupby("trade_date")["ores"].rank(pct=True)
    d["is_top"] = d.pct >= thr
    g = d.groupby(["trade_date", "is_top"])[horizon].mean().unstack("is_top")
    if True not in g.columns or False not in g.columns:
        return None
    dd = (g[True] - g[False]).dropna()
    ss, nm = monthly_sign(dd, np.sign(dd.mean()) if dd.mean() != 0 else 1.0)
    return dict(delta=float(dd.mean()), samesign=ss, nmonths=nm,
                mean_top=float(d[d.is_top][horizon].mean()),
                mean_rest=float(d[~d.is_top][horizon].mean()))


def main():
    df = load()
    print(f"panel rows={len(df):,} regime 覆盖 {df.regime.notna().mean():.3f}")
    lab = json.loads(SNAP.read_text())
    lab = pd.Series(lab)
    print("全样本 regime 分布:\n", lab.value_counts().to_string(), "\n")

    rep = {}
    for wname, (a, b) in WINDOWS.items():
        sub = df[(df.trade_date >= a) & (df.trade_date <= b)]
        for regime in ("强牛", "ALL"):
            s = sub if regime == "ALL" else sub[sub.regime == regime]
            if not len(s):
                continue
            key = f"{wname}|{regime}"
            print(f"\n===================== {key}  n={len(s):,} days={s.trade_date.nunique()} =====================")
            print(f"{'factor':<34}{'Q1':>9}{'Q5':>9}{'Q10':>9}{'Q10-Q1':>9}{'大亏Q1':>8}{'大亏Q5':>8}{'大亏Q10':>8}")
            for label, col in FACTORS:
                if col not in s.columns:
                    continue
                r = decile_table(s, col)
                if r is None:
                    print(f"{label:<34} 样本不足")
                    continue
                rep[f"{key}|{col}|decile"] = r
                q = r["qmeans"]
                print(f"{label:<34}{q[0]:>+9.3f}{q[4]:>+9.3f}{q[9]:>+9.3f}{r['spread']:>+9.3f}"
                      f"{r['bad'][0]*100:>8.1f}{r['bad'][4]*100:>8.1f}{r['bad'][9]*100:>8.1f}")

    print("\n\n############ 强牛日 极端尾部 top20% 对照（delta = top - rest, fwd5） ############")
    print(f"{'factor':<34}" + "".join(f"{w:>26}" for w in WINDOWS))
    for label, col in FACTORS:
        line = f"{label:<34}"
        for wname, (a, b) in WINDOWS.items():
            s = df[(df.trade_date >= a) & (df.trade_date <= b) & (df.regime == "强牛")]
            r = tail_compare(s, col) if len(s) else None
            if r is None:
                line += f"{'--':>26}"
            else:
                rep[f"{wname}|强牛|{col}|tail"] = r
                line += f"{r['delta']:>+10.3f}(ss{r['samesign']:.2f},n{r['n_top']:>4})".rjust(26)
        print(line)

    print("\n############ 增量检验：残差尾部 top20%，对 ivol20 逐日正交化后 ############")
    print(f"{'factor(正交于ivol20)':<30}" + "".join(f"{w:>26}" for w in WINDOWS))
    for label, col in FACTORS:
        if col == "ivol20":
            continue
        line = f"{label[:28]:<30}"
        for wname, (a, b) in WINDOWS.items():
            s = df[(df.trade_date >= a) & (df.trade_date <= b) & (df.regime == "强牛")]
            r = ortho_residual_tail(s, col, "ivol20") if len(s) else None
            if r is None:
                line += f"{'--':>26}"
            else:
                rep[f"{wname}|强牛|{col}|ortho_ivol20"] = r
                line += f"{r['delta']:>+10.3f}(ss{r['samesign']:.2f})".rjust(26)
        print(line)
    print("\n############ 反向增量：ivol20 对 ivol_dn20 正交化后 ############")
    for wname, (a, b) in WINDOWS.items():
        s = df[(df.trade_date >= a) & (df.trade_date <= b) & (df.regime == "强牛")]
        r = ortho_residual_tail(s, "ivol20", "ivol_dn20") if len(s) else None
        if r:
            rep[f"{wname}|强牛|ivol20|ortho_ivol_dn20"] = r
            print(f"  {wname}: Δtop20%={r['delta']:+.3f} 同号{r['samesign']:.2f} n{r['nmonths']}m "
                  f"top{r['mean_top']:+.3f} rest{r['mean_rest']:+.3f}")

    # 相关性
    print("\n############ 强牛日 逐日横截面 Spearman 相关性（中位数） ############")
    for wname, (a, b) in WINDOWS.items():
        s = df[(df.trade_date >= a) & (df.trade_date <= b) & (df.regime == "强牛")]
        cols = ["ivol20", "ivol_dn20", "ivol_up20", "ivol_asym20", "beta_dn20", "worst_resid20"]
        s = s[["trade_date"] + cols].dropna()
        corrs = []
        for c1, c2 in itertools.combinations(cols, 2):
            if c1 == "ivol20" or c2 == "ivol20" or (c1, c2) in [("ivol_dn20", "ivol_up20")]:
                r = s.groupby("trade_date").apply(lambda g: g[c1].corr(g[c2], method="spearman"))
                corrs.append((wname, c1, c2, float(r.median())))
        for w, c1, c2, v in corrs:
            if c1 == "ivol20" or c2 == "ivol20":
                print(f"  {wname} {c1:>14} vs {c2:<14} rho={v:+.3f}")

    (OUT / "resid_downside_screen.json").write_text(json.dumps(
        rep, ensure_ascii=False, indent=2, default=lambda o: float(o) if hasattr(o, "item") else str(o)))
    print("\nsaved output/research/resid_downside_screen.json")


if __name__ == "__main__":
    main()
