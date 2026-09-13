"""外部指数慢趋势【全日门控】三窗口 canonical 风格终审（只读）。
门控：信号日 000905 收盘在 MA20 下方则当日不选出（等价生产择时门控，整日空仓记0）。
对照 baseline v2；逐笔/日cohort/配对日差 bootstrap CI；并测 strong_down、ix_ret10 阈值变体。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
OUT = B / "output" / "research"
SLEEVE = 1 / 6


def boot(d, draws=5000, seed=7):
    rng = np.random.default_rng(seed); d = np.asarray(d, float); n = len(d)
    if n < 3: return (np.nan, np.nan)
    return tuple(np.percentile([d[rng.integers(0, n, n)].mean() for _ in range(draws)], [2.5, 97.5]))


def coh(df, dates):
    return df.groupby("dt").net.mean().reindex(dates).fillna(0)


def eval_gate(name, df, keep_mask):
    dates = sorted(df.dt.unique())
    base, gated = df, df[keep_mask(df)]
    cb, cg = coh(base, dates), coh(gated, dates)
    touched = sorted(set(base.dt) - set(gated.dt))
    chg = (cg - cb).loc[touched]
    lo, hi = boot(chg.values)
    removed = base[~base.dt.isin(set(gated.dt))]
    print(f"[{name}] 逐笔 {base.net.mean():+.4f}(n{len(base)}) -> {gated.net.mean():+.4f}(n{len(gated)}) | "
          f"日cum(SLEEVE) {cb.sum()*SLEEVE:+.2f}->{cg.sum()*SLEEVE:+.2f} | "
          f"空出{len(touched)}天 这些天均{removed.net.mean():+.3f}/胜{(removed.net>0).mean():.2f} | "
          f"配对Δ{chg.mean():+.4f} CI[{lo:+.4f},{hi:+.4f}] 月正"
          f"{(chg.groupby(lambda d:d[:7]).sum()>0).sum()}-{(chg.groupby(lambda d:d[:7]).sum()<0).sum()}")


def main():
    W = {"FROZEN": pd.read_pickle(OUT / "ixgate_frozen.pkl"),
         "FORWARD": pd.read_pickle(OUT / "ixgate_forward.pkl"),
         "RUN3": pd.read_pickle(OUT / "ixgate_run3.pkl")}
    gates = {
        "above20(只在MA20上出票)": lambda d: d.above20 == 1,
        "not_strong_down(非双均线下方)": lambda d: d.strong_down == 0,
        "ix_ret10>=-3%(10日指数未急跌)": lambda d: d.ix_ret10 >= -0.03,
        "dist20>=-2%(不在MA20下2%外)": lambda d: d.dist20 >= -0.02,
    }
    for gname, mk in gates.items():
        print(f"\n===== 门控: {gname} =====")
        for w, d in W.items():
            eval_gate(w, d, mk)


if __name__ == "__main__":
    main()
