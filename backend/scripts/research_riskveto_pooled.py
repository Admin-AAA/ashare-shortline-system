"""ivol 强牛门控否决：三窗口合并的配对日显著性 + 逐日符号 + 累计改善（只读，生产同构）。"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.production_policy import load_production_selection_policy
from app.engine.backtest import ExecutionPolicy
from app.engine.aggregator import _market_base
from scripts.research_riskveto_canonical import (
    factor_pct_lookup, frozen_groups, canon_groups, _run_window, _metrics)
from scripts.research_canonical_two_window import _bootstrap_ci

THR = 0.80; GATE = {"强牛"}


def daily_delta(groups, pctmap, pol, ep=None):
    base, veto, rem, pro = _run_window(groups, pctmap, THR, pol, ep, GATE)
    dates = sorted({r["dt"] for r in base} | {r["dt"] for r in veto})
    mb, mv = _metrics(base, dates), _metrics(veto, dates)
    j = pd.concat([mb["g"].rename("b"), mv["g"].rename("v")], axis=1).fillna(0.0)
    d = (j.v - j.b)
    return d, np.array(rem), np.array(pro)


def main():
    pctmap = factor_pct_lookup("ivol20")
    pol = load_production_selection_policy(version=2); ep = ExecutionPolicy()
    print("加载分组 ...")
    _, f = frozen_groups(); _, w2 = canon_groups("forward"); _, w3 = canon_groups("third")
    deltas, labels = [], []
    for nm, g, e in [("W1", f, ep), ("W2", w2, None), ("W3", w3, None)]:
        d, rem, pro = daily_delta(g, pctmap, pol, e)
        fired = d[d.abs() > 1e-9]
        lo, hi = _bootstrap_ci(d.values, draws=10000)
        print(f"\n[{nm}] 出票天{len(d)} 实际改动天{len(fired)} | 改动日Δ为正占比{(fired>0).mean():.2f} "
              f"({int((fired>0).sum())}/{len(fired)})")
        print(f"     日Δ合计{d.sum():+.3f} 日均{d.mean():+.4f} bootstrap95CI[{lo:+.4f},{hi:+.4f}] "
              f"| 被砍n{len(rem)}均{rem.mean() if len(rem) else float('nan'):+.3f} 递补n{len(pro)}均{pro.mean() if len(pro) else float('nan'):+.3f}")
        deltas.append(d); labels += [nm] * len(d)
    pool = pd.concat(deltas)
    lo, hi = _bootstrap_ci(pool.values, draws=20000)
    fired = pool[pool.abs() > 1e-9]
    print("\n================ 三窗合并 ================")
    print(f"总出票天{len(pool)} 改动天{len(fired)} 改动日正占比{(fired>0).mean():.2f}({int((fired>0).sum())}/{len(fired)})")
    print(f"日Δ合计{pool.sum():+.3f} 日均{pool.mean():+.4f} 合并bootstrap95CI[{lo:+.4f},{hi:+.4f}]")
    # 分层 block bootstrap：每个窗口内按日重采样→窗内均值→三窗均值等权
    rng = np.random.default_rng(11); bs = []
    arrs = [d.values for d in deltas]
    for _ in range(20000):
        wm = []
        for a in arrs:
            wm.append(a[rng.integers(0, len(a), size=len(a))].mean())
        bs.append(np.mean(wm))
    print(f"分层block-bootstrap95CI[{np.quantile(bs,.025):+.4f},{np.quantile(bs,.975):+.4f}] P(Δ>0)={np.mean(np.array(bs)>0):.3f}")
    print("三窗日Δ均值:", [f"{d.mean():+.4f}" for d in deltas])


if __name__ == "__main__":
    main()
