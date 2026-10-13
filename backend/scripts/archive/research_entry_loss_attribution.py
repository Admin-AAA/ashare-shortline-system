"""入场侧亏损笔归因（canonical 三窗，严格 T 日可得特征）。

目标：用户的 WR≥60% 是"少亏"指标，而第 8 轮已证明出场侧抬均值必然压胜率。
所以这一轮问的是相反的问题——**在 T 日可观测特征里，哪些桶在三个窗口都集中亏损？**
找出来的桶才是"提高胜率"的候选来源。

方法（沿用 round-8 的 canonical 口径）：
1. 选择走真实 `evaluate_selection_policy`（v4 + 预附 ivol 分位）；
2. 出场用 `daily_kbars` 重建 bar + `_simulate_card` 真实因果模拟 → 逐笔净收益；
3. 特征全部取**信号日 T** 的 `daily_kbars` 行（严格无未来函数；不读
   `feature_snapshot_json.market_regime`，遵守 L-013），派生量（chg20/pos60/均线距离）
   用同一 code 的历史行在 T 日截断计算；
4. 每个特征在窗内按分位分 5 桶，报 n/胜率/均净/月一致率，并给出
   「三窗同为负且每窗 n≥25」的候选桶清单。

只读，不改生产代码。
用法：cd backend && .venv/bin/python scripts/research_entry_loss_attribution.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.engine.aggregator import _market_base  # noqa: E402
from app.engine.backtest import ExecutionPolicy  # noqa: E402
from app.services.risk_features import (  # noqa: E402
    load_ivol20_pct_map,
    load_ivol_dn20_pct_map,
)
from scripts.research_canonical_two_window import _bootstrap_ci  # noqa: E402
from scripts.research_riskveto_canonical import frozen_groups, canon_groups  # noqa: E402
from scripts.research_off_main_theme_penalty import (  # noqa: E402
    _main_industries_from_db,
    _attach_main_industries,
)
from scripts.research_exit_policy_canonical import (  # noqa: E402
    _bars_for,
    _calendar,
    _closes_for,
    _selected,
    _sim_day,
)
import scripts.research_exit_policy_canonical as exit_harness  # noqa: E402
from app.engine.production_policy import load_production_selection_policy  # noqa: E402

OUT = B / "output" / "research"
RAW_FEATURES = (
    "pct_chg", "amount", "turnover_rate", "close", "high", "low",
    "rsi_12", "macd_bar", "vol_ratio_5", "vol_ratio_20", "bias_5", "bias_20",
    "chip_cost_15", "chip_cost_50", "chip_cost_85", "chip_winner_rate",
    "breakout20", "breakout60", "max_dd20", "max_dd60",
    "upper_shadow_count20", "profit_efficiency", "atr", "ma5", "ma20", "ma60",
)


def _feature_panel(codes):
    cols = ", ".join(RAW_FEATURES)
    with SessionLocal() as s:
        rows = s.execute(
            text(
                f"select code, trade_date, {cols} from daily_kbars "
                "where code in :cs and trade_date >= '2024-09-01' and close > 0"
            ),
            {"cs": tuple(codes)},
        ).mappings().all()
    df = pd.DataFrame(rows)
    for c in RAW_FEATURES:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values(["code", "trade_date"]).reset_index(drop=True)
    g = df.groupby("code", sort=False)
    df["chg20"] = g["close"].transform(lambda s: s / s.shift(20) - 1.0) * 100
    df["chg5"] = g["close"].transform(lambda s: s / s.shift(5) - 1.0) * 100
    # T 日为止的 60 日位置（严格用 T 及之前，含当日的滚动极值）
    hi60 = g["high"].transform(lambda s: s.rolling(60, min_periods=30).max())
    lo60 = g["low"].transform(lambda s: s.rolling(60, min_periods=30).min())
    df["pos60"] = (df["close"] - lo60) / (hi60 - lo60) * 100
    df["dist_ma5"] = (df["close"] / df["ma5"] - 1.0) * 100
    df["dist_ma20"] = (df["close"] / df["ma20"] - 1.0) * 100
    df["dist_ma60"] = (df["close"] / df["ma60"] - 1.0) * 100
    df["chip_band"] = (df["chip_cost_85"] - df["chip_cost_15"]) / df["chip_cost_15"] * 100
    return df


def _bucket_stats(sub: pd.DataFrame, rest: pd.DataFrame) -> dict:
    if not len(sub):
        return {"n": 0}
    win = float((sub.net > 0).mean())
    gp, gl = sub.net[sub.net > 0].sum(), abs(sub.net[sub.net < 0].sum())
    return {
        "n": int(len(sub)), "win": win, "avg": float(sub.net.mean()),
        "pf": float(gp / gl) if gl > 1e-9 else float("inf"),
    }


def _month_consistency(df: pd.DataFrame, mask: pd.Series) -> float:
    sub, rest = df[mask], df[~mask]
    if not len(sub) or not len(rest):
        return float("nan")
    m = df.assign(bucket=mask).groupby([df.dt.str[:7], "bucket"]).net.mean().unstack()
    if True not in m.columns or False not in m.columns:
        return float("nan")
    d = (m[True] - m[False]).dropna()
    return float((d < 0).mean()) if len(d) else float("nan")


def main() -> None:
    ep = ExecutionPolicy()
    # `_selected` 读该模块的全局策略句柄；这里显式绑定 v4，避免静默用 None。
    exit_harness._POLICY = load_production_selection_policy(version=4)
    cal = _calendar()
    _idx = {d: i for i, d in enumerate(cal)}
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    windows = (("W1", f_dates, f_groups), ("W2", w2_dates, w2_groups), ("W3", w3_dates, w3_groups))
    all_dates = sorted({d for _w, ds, _g in windows for d in ds})
    # `_sim_day` 读该模块的 bar 日历映射；显式填充，避免 KeyError / 静默空 bar。
    exit_harness.bars_dates = {
        d: cal[_idx[d] + 1: _idx[d] + 1 + 9] for d in all_dates if d in _idx
    }
    mi = _main_industries_from_db(all_dates)
    for _w, _d, g in windows:
        _attach_main_industries(g, mi)
    with SessionLocal() as s:
        iv20map = load_ivol20_pct_map(s, all_dates)
        ivdnmap = load_ivol_dn20_pct_map(s, all_dates)
    picks = {w: _selected(g, iv20map, ivdnmap) for w, _d, g in windows}
    codes = sorted({c for w in picks for _d, c in picks[w]})
    bars, closes = {}, {}
    for w, _d, _g in windows:
        need = {x for dt in {d for d, _c in picks[w]} for x in cal[cal.index(dt) + 1: cal.index(dt) + 10]}
        print(f"loading bars for {w} ...", flush=True)
        bars.update(_bars_for(sorted({c for _d, c in picks[w]}), need))
        closes.update(_closes_for(sorted({c for _d, c in picks[w]}), {d for d, _c in picks[w]}))

    trades = []
    for w, dates, groups in windows:
        for dt, _cards, _nm, day in groups:
            sel = [(c, picks[w][(dt, c)]) for (d, c) in picks[w] if d == dt]
            if not sel:
                continue
            ms = dict(getattr(day, "market_snapshot", {}) or {})
            base = _market_base(ms)
            for r in _sim_day(dt, day, sel, ep, bars=bars, closes=closes):
                r["window"] = w
                r["regime"] = base
                trades.append(r)
    tr = pd.DataFrame(trades)
    tr["dt"] = tr.dt.astype(str)
    print(f"trades: {len(tr)}  " + "  ".join(
        f"{w}={int((tr.window == w).sum())}" for w in ("W1", "W2", "W3")), flush=True)

    panel = _feature_panel(codes)
    feats = [c for c in panel.columns if c not in ("code", "trade_date")]
    merged = tr.merge(
        panel.rename(columns={"trade_date": "dt"}),
        on=["code", "dt"], how="left",
    )
    print("features joined:", sum(1 for f in feats if f in merged.columns), flush=True)

    report, candidates, spreads = {}, [], []
    MIN_N = 20  # W3 只有 95 笔，三分位每桶约 31；用 20 保证 W3 也能过
    for feat in feats:
        if feat not in merged.columns:
            continue
        per_window, rows, per_window_spread = {}, [], {}
        for w in ("W1", "W2", "W3"):
            sub = merged[merged.window == w]
            vals = sub[feat].dropna()
            # W3 整窗只有 95 笔（去重前），门槛必须低于它，否则 W3 永远被跳过
            if len(vals) < 60:
                continue
            try:
                q = pd.qcut(sub[feat], 3, labels=False, duplicates="drop")
            except ValueError:
                continue
            bucket_avgs = []
            for b in sorted(pd.Series(q).dropna().unique()):
                mask = q == b
                st = _bucket_stats(sub[mask], sub[~mask])
                if st.get("n", 0) < MIN_N:
                    continue
                st["bucket"] = int(b)
                st["lo"] = float(sub.loc[mask, feat].min())
                st["hi"] = float(sub.loc[mask, feat].max())
                st["month_cons"] = _month_consistency(sub, mask)
                st["window"] = w
                per_window.setdefault(b, {})[w] = st
                rows.append(st)
                bucket_avgs.append((st["avg"], st["n"]))
            if len(bucket_avgs) >= 2:
                per_window_spread[w] = max(a for a, _ in bucket_avgs) - min(a for a, _ in bucket_avgs)
        if not rows:
            continue
        report[feat] = rows
        if len(per_window_spread) == 3:
            spreads.append({
                "feature": feat,
                "spread": {w: round(v, 4) for w, v in per_window_spread.items()},
                "min_spread": round(min(per_window_spread.values()), 4),
            })
        for b, byw in per_window.items():
            if len(byw) != 3:
                continue
            if all(v["avg"] < 0 for v in byw.values()) and min(v["n"] for v in byw.values()) >= MIN_N:
                candidates.append({
                    "feature": feat, "bucket": b,
                    "range": [byw["W1"]["lo"], byw["W1"]["hi"]],
                    "per_window": {w: {"n": v["n"], "win": round(v["win"], 3),
                                       "avg": round(v["avg"], 4), "month_cons": round(v["month_cons"], 2)}
                                   for w, v in byw.items()},
                })

    spreads.sort(key=lambda x: -x["min_spread"])
    print("\n=== 特征区分度（三桶内 best-worst 均净差，按三窗最小差排序）top15 ===")
    for s in spreads[:15]:
        print(f"{s['feature']:22s} W1 {s['spread']['W1']:+.4f} | W2 {s['spread']['W2']:+.4f} "
              f"| W3 {s['spread']['W3']:+.4f} | min {s['min_spread']:+.4f}")

    print(f"\n=== 三窗同为负、每窗 n≥{MIN_N} 的候选桶 ===")
    if not candidates:
        print("（无）")
    for c in candidates:
        print(f"{c['feature']:22s} Q{c['bucket']} [{c['range'][0]:.3f},{c['range'][1]:.3f}] " + " | ".join(
            f"{w} n{v['n']} 胜{v['win']} 均{v['avg']} 月一致{v['month_cons']}"
            for w, v in c["per_window"].items()))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "entry_loss_attribution.json").write_text(
        json.dumps({"candidates": candidates, "spreads": spreads, "by_feature": report},
                   ensure_ascii=False, indent=2, default=str))
    print(f"\nsaved {OUT / 'entry_loss_attribution.json'}")


if __name__ == "__main__":
    main()
