"""涨停板情绪微观结构 → 市场级择时 overlay 的决定性因果检验（只读）。

假设（L-065 合法出路②框架/regime 识别）：生产 regime 只用当日上涨比、情绪只用涨停率
与涨跌停数量比，从未使用炸板率/连板高度/梯队。这些「亏钱效应/情绪周期退潮」信号在信号日
T 收盘后已知（T+1 开盘入场前），或可预测当日生产式候选 cohort 未来 5 日因果净收益。

严格性对齐项目门禁（research_intraday_portfolio_levers 的 shadow gate）：
- 生产因果出场引擎 _resolve_exit_causal（-4.5/+6/profit_floor/费用/滑点/T+1 开盘/跳空过滤）；
- 生产式日度 cohort：final_score>=72 按分排序取 top6 等权（与 harness 同口径）；
- 60% 时间切分训练/OOS，阈值只从训练集取；Bonferroni 校正 bootstrap CI；逐月同号>=80%；
- 必须证明对【现有 regime(上涨比)/情绪(涨停率)】有增量（残差化 + 分 regime）；
- 退潮日 abstain 反事实按 L-015 组合级测量（abstain=现金），报利用率/机会成本符号/MDD。

只读，不写生产。
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.research_intraday_portfolio_levers import (  # type: ignore
    _load_snapshot, _simulate_card, SCORE_FLOOR, TOP_N,
    TRAIN_FRACTION, DAILY_SLEEVE_FRACTION, _metric_summary, _bootstrap_mean_ci,
)
from app.engine.aggregator import _market_base, _market_sentiment  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "output" / "research"
PANEL = OUT / "limit_sentiment_panel.pkl"

# 预注册特征：(列名, 假设方向 +1=越大 cohort 越好 / -1=越大越差, 中文)
FEATURES = [
    ("broken_rate_ma3", -1, "炸板率(宽,3日均)"),
    ("broken_rate_strict_ma3", -1, "炸板率(严格,3日均)"),
    ("seal_rate_ma3", +1, "封板成功率(3日均)"),
    ("height_ma3", +1, "连板高度(3日均)"),
    ("d_max_height", +1, "连板高度日变化"),
    ("ladder2", +1, "2板及以上家数"),
    ("d_lu_n", +1, "涨停家数日变化"),
    ("lu_ld_ratio", +1, "涨停/跌停比"),
    ("ld_n", -1, "跌停家数"),
    ("d_broken_rate", -1, "炸板率日变化"),
]
N_FEAT = len(FEATURES)


def build_cohort_frame() -> pd.DataFrame:
    days = _load_snapshot()
    sim = {}
    for day in days:
        snap = day.get("market_snapshot") or {}
        for card in day.get("cards") or []:
            t = _simulate_card(day, card)
            if t is not None:
                sim[(t.signal_date, t.code)] = t
    rows = []
    for day in days:
        date = str(day["signal_date"])
        snap = day.get("market_snapshot") or {}
        eligible = []
        for card in day.get("cards") or []:
            t = sim.get((date, str(card.get("code"))))
            if t is None or float(card.get("final_score") or 0) < SCORE_FLOOR:
                continue
            eligible.append((float(card.get("final_score") or 0), t))
        if not eligible:
            # 仍记录一行（无票日），其情绪状态用于覆盖率分析，但 basket 为 NaN
            rows.append({"signal_date": date, "basket": np.nan, "n_taken": 0,
                         "market_base": _market_base(snap), "sentiment": _market_sentiment(snap),
                         "up_ratio": snap.get("up_count", 0) / max(snap.get("sample_count") or 1, 1),
                         "lu_rate": snap.get("limit_up_count", 0) / max(snap.get("sample_count") or 1, 1)})
            continue
        eligible.sort(key=lambda x: -x[0])
        top = [t for _, t in eligible[:TOP_N]]
        basket = float(np.mean([t.net_return_pct for t in top]))
        allret = float(np.mean([t.net_return_pct for _, t in eligible]))
        rows.append({"signal_date": date, "basket": basket, "all_ge_floor": allret,
                     "n_taken": len(top), "n_eligible": len(eligible),
                     "market_base": _market_base(snap), "sentiment": _market_sentiment(snap),
                     "up_ratio": snap.get("up_count", 0) / max(snap.get("sample_count") or 1, 1),
                     "lu_rate": snap.get("limit_up_count", 0) / max(snap.get("sample_count") or 1, 1)})
    coh = pd.DataFrame(rows).sort_values("signal_date")
    panel = pd.read_pickle(PANEL)
    coh = coh.merge(panel, left_on="signal_date", right_on="trade_date", how="left")
    return coh


def _oriented_tercile_test(coh: pd.DataFrame, col: str, direction: int, split: str) -> dict:
    d = coh.dropna(subset=[col, "basket"]).copy()
    tr, oo = d[d.signal_date <= split], d[d.signal_date > split]
    if len(tr) < 30 or len(oo) < 20:
        return {"note": "insufficient"}
    qlo, qhi = tr[col].quantile(1/3), tr[col].quantile(2/3)
    def grp(x):
        return np.where(x[col] <= qlo, "low", np.where(x[col] >= qhi, "high", "mid"))
    tr = tr.assign(g=grp(tr)); oo = oo.assign(g=grp(oo))
    def means(df):
        return {g: round(float(df[df.g == g]["basket"].mean()), 4) if (df.g == g).any() else None
                for g in ["low", "mid", "high"]}
    trm, oom = means(tr), means(oo)
    # 方向化 high-low：direction=+1 时 high 应更好；-1 时 low 应更好
    def delta(m):
        if m["high"] is None or m["low"] is None:
            return np.nan
        return direction * (m["high"] - m["low"])
    tr_delta, oo_delta = delta(trm), delta(oom)
    # 逐月同号：每月内 high-low（方向化）符号
    d2 = d.copy(); d2["g"] = grp(d2); d2["month"] = d2.signal_date.str[:7]
    mo = []
    for m_, gp in d2.groupby("month"):
        hi = gp[gp.g == "high"]["basket"]; lo = gp[gp.g == "low"]["basket"]
        if len(hi) and len(lo):
            mo.append(direction * (hi.mean() - lo.mean()))
    mo = np.array(mo)
    cons = float((mo > 0).mean()) if len(mo) else np.nan
    # OOS high vs low 日级 bootstrap（方向化差值），Bonferroni
    hi_v = direction * oo[oo.g == "high"]["basket"].to_numpy(float)
    lo_v = direction * oo[oo.g == "low"]["basket"].to_numpy(float)
    rng = np.random.default_rng(20260911)
    boots = []
    if len(hi_v) >= 3 and len(lo_v) >= 3:
        for _ in range(4000):
            boots.append(rng.choice(hi_v, len(hi_v), replace=True).mean()
                         - rng.choice(lo_v, len(lo_v), replace=True).mean())
    ci_lo = float(np.quantile(boots, 0.025)) if boots else np.nan
    adj = 1 - 0.05 / N_FEAT
    ci_lo_adj = float(np.quantile(boots, (1-adj)/2)) if boots else np.nan
    # 全样本 spearman（方向化）
    ic = direction * float(d[col].corr(d["basket"], method="spearman"))
    passed = bool(
        tr_delta > 0 and oo_delta > 0 and (not np.isnan(cons)) and cons >= 0.80
        and not np.isnan(ci_lo_adj) and ci_lo_adj > 0
    )
    return {"train_tercile": trm, "oos_tercile": oom, "train_delta": round(tr_delta, 4),
            "oos_delta": round(oo_delta, 4), "month_consistency": round(cons, 3), "months": len(mo),
            "ic_spearman_dir": round(ic, 4), "oos_ci95_lo": round(ci_lo, 4),
            "oos_bonf_ci_lo": round(ci_lo_adj, 4), "oos_hi_n": int((oo.g == "high").sum()),
            "oos_lo_n": int((oo.g == "low").sum()), "passes_gate": passed,
            "q_lo": round(float(qlo), 4), "q_hi": round(float(qhi), 4)}


def orthogonality(coh: pd.DataFrame, col: str, split: str) -> dict:
    """对现有 regime(上涨比)/情绪(涨停率) 的增量：相关 + 残差化后 OOS 是否仍有效。"""
    d = coh.dropna(subset=[col, "basket"]).copy()
    corr_up = float(d[col].corr(d["up_ratio"], method="spearman"))
    corr_lu = float(d[col].corr(d["lu_rate"], method="spearman"))
    tr, oo = d[d.signal_date <= split], d[d.signal_date > split]
    # 用训练集线性回归剔除 up_ratio + lu_rate，取残差
    Xtr = np.column_stack([np.ones(len(tr)), tr.up_ratio, tr.lu_rate])
    beta = np.linalg.lstsq(Xtr, tr[col], rcond=None)[0]
    def resid(df):
        X = np.column_stack([np.ones(len(df)), df.up_ratio, df.lu_rate])
        return df[col].to_numpy() - X @ beta
    d = d.copy()
    d["resid"] = np.concatenate([resid(tr), resid(oo)]) if len(oo) else resid(tr)
    rtr, roo = d[d.signal_date <= split], d[d.signal_date > split]
    ql = rtr.resid.quantile(1/3); qh = rtr.resid.quantile(2/3)
    def topbot(df):
        hi = df[df.resid >= qh]["basket"]; lo = df[df.resid <= ql]["basket"]
        return (float(hi.mean()) if len(hi) else np.nan, float(lo.mean()) if len(lo) else np.nan, len(hi), len(lo))
    trh, trl, _, _ = topbot(rtr); ooh, ool, noh, nol = topbot(roo)
    return {"corr_up_ratio": round(corr_up, 3), "corr_lu_rate": round(corr_lu, 3),
            "resid_train_hi_lo": round(trh-trl, 4), "resid_oos_hi_lo": round(ooh-ool, 4),
            "resid_oos_n": [noh, nol]}


def composite_ebb(coh: pd.DataFrame, split: str) -> tuple[pd.DataFrame, dict]:
    """理论预注册退潮分数：炸板率↑ + 高度↓ + 涨停家数↓ + 跌停↑ → 越毒。
    z 分数只用训练集均值/标准差（防泄漏）。"""
    cols = ["broken_rate_ma3", "height_ma3", "d_lu_n", "ld_n"]
    d = coh.dropna(subset=cols + ["basket"]).copy()
    tr = d[d.signal_date <= split]
    mu = {c: tr[c].mean() for c in cols}; sd = {c: tr[c].std(ddof=0) for c in cols}
    def z(df, c):
        return (df[c] - mu[c]) / (sd[c] if sd[c] > 0 else 1e-9)
    d["ebb"] = z(d, "broken_rate_ma3") - z(d, "height_ma3") - z(d, "d_lu_n") + (d["ld_n"] - mu["ld_n"]) / (sd["ld_n"] + 1e-9)
    t = _oriented_tercile_test(d.rename(columns={"ebb": "_ebb"}).assign(broken_rate_ma3=d["broken_rate_ma3"]),
                               "_ebb", -1, split) if False else None
    # 直接对 ebb 做方向化（ebb 越大越毒 → direction=-1）
    tt = _oriented_tercile_test(d, "ebb", -1, split)
    return d, tt


def abstain_counterfactual(coh: pd.DataFrame, ebb_col: str, split: str) -> dict:
    d = coh.dropna(subset=[ebb_col, "basket"]).copy()
    tr = d[d.signal_date <= split]
    qhi = tr[ebb_col].quantile(2/3)  # 训练集：退潮分最高 1/3 为毒区
    out = {"toxic_cut": round(float(qhi), 4)}
    for window, dd in [("all", d), ("oos", d[d.signal_date > split]), ("train", d[d.signal_date <= split])]:
        base = _metric_summary(dd["basket"])
        toxic = dd[dd[ebb_col] >= qhi]
        safe = dd[dd[ebb_col] < qhi]
        challenger = dd["basket"].where(dd[ebb_col] < qhi, 0.0)  # 毒日 abstain=现金
        chal = _metric_summary(challenger)
        out[window] = {
            "days": int(len(dd)),
            "baseline": base,
            "challenger_abstain_toxic": chal,
            "toxic_days": int(len(toxic)),
            "toxic_day_mean_net": round(float(toxic["basket"].mean()), 4),
            "safe_day_mean_net": round(float(safe["basket"].mean()), 4),
            "invested_day_pct": round(float((dd[ebb_col] < qhi).mean() * 100), 1),
            "opportunity_cost_sign": "避损(毒日均值<0)" if toxic["basket"].mean() < 0 else "踏空(毒日均值>=0)",
            "by_regime_toxic_mean": {k: round(float(v["basket"].mean()), 4)
                                     for k, v in toxic.groupby("market_base") if len(v) >= 3},
        }
    return out


def main():
    coh = build_cohort_frame()
    investable = coh.dropna(subset=["basket"])
    dates = sorted(investable.signal_date.unique())
    split = dates[max(1, int(len(dates) * TRAIN_FRACTION)) - 1]
    print(f"cohort days total={len(coh)} investable={len(investable)} range={dates[0]}..{dates[-1]} split={split}")
    print(f"baseline cohort mean net/day = {investable.basket.mean():.4f}%  positive-day={ (investable.basket>0).mean()*100:.1f}%")
    print("existing regime distribution:", investable.market_base.value_counts().to_dict())
    print("existing sentiment distribution:", investable.sentiment.value_counts().to_dict())

    report = {"split": split, "n_days": len(investable),
              "baseline_mean": round(float(investable.basket.mean()), 4),
              "feature_screen": {}, "orthogonality": {}}
    print("\n===== 预注册特征筛选（方向化 OOS high-low，Bonferroni CI，逐月同号）=====")
    print(f"{'feature':24s} {'trΔ':>7} {'oosΔ':>7} {'moCons':>6} {'IC':>7} {'CIlo_bonf':>9} {'pass':>5}  oos(low/mid/high)")
    for col, dirn, label in FEATURES:
        r = _oriented_tercile_test(coh, col, dirn, split)
        o = orthogonality(coh, col, split)
        report["feature_screen"][col] = {"label": label, **r}
        report["orthogonality"][col] = o
        if r.get("note"):
            print(f"{col:24s} {r['note']}"); continue
        print(f"{col:24s} {r['train_delta']:>7.3f} {r['oos_delta']:>7.3f} {r['month_consistency']:>6.2f} "
              f"{r['ic_spearman_dir']:>7.3f} {r['oos_bonf_ci_lo']:>9.3f} {str(r['passes_gate']):>5}  "
              f"{r['oos_tercile']['low']}/{r['oos_tercile']['mid']}/{r['oos_tercile']['high']}  corr(up,lu)={o['corr_up_ratio']},{o['corr_lu_rate']} residOOSΔ={o['resid_oos_hi_lo']}")

    print("\n===== 理论预注册「退潮分数」ebb =====")
    ebb_df, ebb_res = composite_ebb(coh, split)
    report["composite_ebb"] = ebb_res
    print(json.dumps(ebb_res, ensure_ascii=False, indent=2))

    print("\n===== 退潮日 abstain 反事实（L-015 组合级，abstain=现金）=====")
    cf = abstain_counterfactual(ebb_df, "ebb", split)
    report["abstain_counterfactual"] = cf
    for window in ["train", "oos", "all"]:
        w = cf[window]
        b, c = w["baseline"], w["challenger_abstain_toxic"]
        print(f"[{window}] days={w['days']} toxic={w['toxic_days']}({100-w['invested_day_pct']:.0f}%) "
              f"毒日均{w['toxic_day_mean_net']:+.3f} 安全日均{w['safe_day_mean_net']:+.3f} {w['opportunity_cost_sign']}")
        print(f"    baseline : cum {b['cumulative_pct']:+.2f}% MDD {b['max_drawdown_pct']:.2f}% posDay {b['positive_day_pct']:.1f}% avg {b['avg_cohort_return_pct']:+.4f}")
        print(f"    challenger: cum {c['cumulative_pct']:+.2f}% MDD {c['max_drawdown_pct']:.2f}% posDay {c['positive_day_pct']:.1f}% avg {c['avg_cohort_return_pct']:+.4f}")
        print(f"    by regime toxic mean: {w['by_regime_toxic_mean']}")

    # 三段时间稳健性：每段内毒日 vs 安全日均值
    print("\n===== 三段时间稳健性（毒日 vs 安全日 均净收益）=====")
    ebb_df = ebb_df.sort_values("signal_date").reset_index(drop=True)
    blocks = np.array_split(np.arange(len(ebb_df)), 3)
    robust = []
    for i, idx in enumerate(blocks, 1):
        bl = ebb_df.iloc[idx]
        cut = report["abstain_counterfactual"]["toxic_cut"]
        t = bl[bl.ebb >= cut]["basket"]; s = bl[bl.ebb < cut]["basket"]
        row = {"block": i, "start": bl.signal_date.iloc[0], "end": bl.signal_date.iloc[-1],
               "toxic_mean": round(float(t.mean()), 4), "safe_mean": round(float(s.mean()), 4),
               "toxic_n": int(len(t)), "safe_n": int(len(s))}
        robust.append(row)
        print(f"  block{i} {row['start']}..{row['end']} 毒日={row['toxic_n']} 均{row['toxic_mean']:+.3f} | 安全 n={row['safe_n']} 均{row['safe_mean']:+.3f} | 差 {(row['safe_mean']-row['toxic_mean']):+.3f}")
    report["robustness_blocks"] = robust

    p = OUT / "limit_sentiment_timing.json"
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    coh.to_pickle(OUT / "limit_sentiment_cohort.pkl")
    print("\nsaved:", p)


if __name__ == "__main__":
    main()
