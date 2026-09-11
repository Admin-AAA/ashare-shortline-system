"""Phase-2 决定性检验：隔夜/日内分解因子在【真实候选池】上的因果选股增量。

全市场（Phase-1）有效不等于选中池有效（L-064 选择效应）。本脚本：
1. 用生产因果出场引擎 _resolve_exit_causal（-4.5 止损/+6 止盈/移动止盈/费用/滑点/
   T+1 开盘买入/最大跳空过滤）逐候选算净收益——与 research_intraday 同口径；
2. 在 final_score>=SCORE_FLOOR 的真实可选候选上，用信号日(<=T)日线分解因子做
   横截面排序：日度多空价差、Spearman IC、逐月同号率、训练/OOS；
3. 选股反事实：基线=全部达线候选(等权日度cohort)，挑战者=剔除"日内冲高"最高组/
   按因子重排，比较均净收益/胜率/盈亏比/保留率（只改"选哪些"，不做仓位）。

只读，不写生产。
"""
from __future__ import annotations
import json
from dataclasses import asdict
from pathlib import Path
import numpy as np
import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.research_intraday_portfolio_levers import (  # type: ignore
    _load_snapshot, _simulate_card, SCORE_FLOOR, TRAIN_FRACTION,
)

OUT = Path(__file__).resolve().parents[1] / "output" / "research"
COMPACT = OUT / "factor_compact.pkl"

FACTORS = ["id_20", "on_minus_id_20", "id_10", "on_20", "id_5", "on_10",
           "id_vol_20", "gap_down_tail_20", "chg20", "pos60", "vol_ratio_5",
           "clv_10", "amp_10", "up_shadow_10"]


def build_trade_frame() -> pd.DataFrame:
    days = _load_snapshot()
    sim = {}
    for day in days:
        for card in day.get("cards") or []:
            t = _simulate_card(day, card)
            if t is not None:
                sim[(t.signal_date, t.code)] = t
    trades = pd.DataFrame(asdict(t) for t in sim.values())
    fac = pd.read_pickle(COMPACT)
    fac = fac.drop(columns=[c for c in ["open", "close", "amount", "is_st", "is_bj", "list_date"] if c in fac.columns])
    m = trades.merge(fac, left_on=["signal_date", "code"], right_on=["trade_date", "code"], how="left")
    return m


def daily_ic_spread(g: pd.DataFrame, factor: str) -> pd.DataFrame:
    rows = []
    for date, gp in g.groupby("signal_date"):
        gp = gp.dropna(subset=[factor])
        if len(gp) < 4 or gp[factor].nunique() < 3:
            continue
        rk = gp[factor].rank(pct=True)
        hi = gp[rk >= 2 / 3]["net_return_pct"]
        lo = gp[rk <= 1 / 3]["net_return_pct"]
        if len(hi) < 1 or len(lo) < 1:
            continue
        ic = gp[factor].corr(gp["net_return_pct"], method="spearman")
        rows.append({
            "signal_date": date, "month": date[:7], "ic": ic,
            "hi": hi.mean(), "lo": lo.mean(), "spread": hi.mean() - lo.mean(),
            "n": len(gp),
        })
    return pd.DataFrame(rows)


def factor_table(m: pd.DataFrame, pool, split: str) -> list[dict]:
    out = []
    g = m[m["final_score"] >= pool].copy()
    for fac in FACTORS:
        t = daily_ic_spread(g, fac)
        if t.empty:
            continue
        tr, oo = t[t.signal_date <= split], t[t.signal_date > split]
        dirn = 1 if t.spread.mean() >= 0 else -1
        mc = t.groupby("month").spread.mean()
        cons = float((np.sign(mc) == dirn).mean())
        out.append({
            "factor": fac, "pool": pool, "days": len(t),
            "spread_pct": round(float(t.spread.mean()), 4),
            "ic_mean": round(float(t.ic.mean()), 4),
            "ic_t": round(float(t.ic.mean() / (t.ic.std() / np.sqrt(t.ic.notna().sum()))), 2),
            "month_consistency": round(cons, 3),
            "train_spread": round(float(tr.spread.mean()), 4),
            "oos_spread": round(float(oo.spread.mean()), 4),
            "oos_month_cons": round(float((np.sign(oo.groupby('month').spread.mean()) == dirn).mean()), 3) if len(oo) else None,
            "hi_mean": round(float(t.hi.mean()), 4), "lo_mean": round(float(t.lo.mean()), 4),
        })
    return out


def cohort_daily(g: pd.DataFrame) -> pd.Series:
    return g.groupby("signal_date")["net_return_pct"].mean()


def stats(cohort: pd.Series) -> dict:
    if len(cohort) == 0:
        return {}
    return {
        "days": int(len(cohort)),
        "avg_net_pct": round(float(cohort.mean()), 4),
        "positive_day_pct": round(float((cohort > 0).mean() * 100), 2),
    }


def selection_counterfactual(m: pd.DataFrame, pool: float, split: str) -> dict:
    """基线 vs 剔除 id_20 最高组 / 偏好低 id_20 的选股反事实（OOS 与全期）。"""
    g = m[m["final_score"] >= pool].copy()
    res = {}
    for window, dd in [("all", g), ("oos", g[g.signal_date > split]), ("train", g[g.signal_date <= split])]:
        rows = {}
        rows["baseline_all"] = stats(cohort_daily(dd))
        # 日内冲高（id_20）最高 1/3 剔除
        dd = dd.copy()
        dd["rk_id20"] = dd.groupby("signal_date")["id_20"].rank(pct=True)
        keep = dd[dd.rk_id20 < 2 / 3]
        rows["drop_top_id20_third"] = stats(cohort_daily(keep))
        rows["drop_top_id20_third"]["kept_pct"] = round(len(keep) / max(len(dd), 1) * 100, 1)
        # 只保留 id_20 最低 1/2（日内最克制的一半）
        keep_low = dd[dd.rk_id20 <= 0.5]
        rows["keep_low_id20_half"] = stats(cohort_daily(keep_low))
        rows["keep_low_id20_half"]["kept_pct"] = round(len(keep_low) / max(len(dd), 1) * 100, 1)
        # on_minus_id_20 最高 2/3（隔夜强、日内克制）
        dd["rk_om"] = dd.groupby("signal_date")["on_minus_id_20"].rank(pct=True)
        keep_om = dd[dd.rk_om >= 1 / 3]
        rows["keep_high_on_minus_id"] = stats(cohort_daily(keep_om))
        rows["keep_high_on_minus_id"]["kept_pct"] = round(len(keep_om) / max(len(dd), 1) * 100, 1)
        # 弱市单独：剔除 id_20 最高组
        weak = dd[dd.market_base == "弱势"]
        if len(weak) >= 30:
            weak_base = stats(cohort_daily(weak))
            weak_keep = weak[weak.rk_id20 < 2 / 3]
            rows["weak_baseline"] = weak_base
            rows["weak_drop_top_id20"] = stats(cohort_daily(weak_keep))
        res[window] = rows
    return res


def main():
    m = build_trade_frame()
    print(f"simulated candidate trades merged: {len(m)}; with id_20: {m['id_20'].notna().sum()}")
    dates = sorted(m.signal_date.unique())
    split = dates[max(1, int(len(dates) * TRAIN_FRACTION)) - 1]
    print("split:", split, "range", dates[0], dates[-1])

    report = {"split": split, "score_floor": SCORE_FLOOR, "by_pool": {}, "counterfactual": {}}
    for pool in [72.0, 68.0, 60.0]:
        tbl = factor_table(m, pool, split)
        tbl.sort(key=lambda r: abs(r["ic_t"]), reverse=True)
        report["by_pool"][str(pool)] = tbl
        print(f"\n===== 候选池 final_score>={pool}  (n days per factor) =====")
        print(f"{'factor':18s} {'spread%':>8} {'ic':>7} {'ic_t':>6} {'moCons':>6} {'tr%':>7} {'oos%':>7} {'oosCon':>6} {'hi':>6}/{'lo':>6}")
        for r in tbl:
            print(f"{r['factor']:18s} {r['spread_pct']:>8.3f} {r['ic_mean']:>7.4f} {r['ic_t']:>6.1f} {r['month_consistency']:>6.2f} {r['train_spread']:>7.3f} {r['oos_spread']:>7.3f} {str(r['oos_month_cons']):>6} {r['hi_mean']:>6.2f}/{r['lo_mean']:>6.2f}")

    cf = selection_counterfactual(m, 72.0, split)
    report["counterfactual"] = cf
    print("\n\n===== 选股反事实（score>=72，日度等权 cohort 均净收益%） =====")
    for window, rows in cf.items():
        print(f"\n[{window}]")
        for k, v in rows.items():
            print(f"  {k:24s} {v}")

    p = OUT / "candidate_decomposition.json"
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    # 也存合并明细供后续
    m.to_pickle(OUT / "candidate_trades_with_factors.pkl")
    print("\nsaved:", p)


if __name__ == "__main__":
    main()
