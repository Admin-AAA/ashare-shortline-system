"""Phase-1 全市场因子筛选：隔夜/日内收益分解等"未挖维度"。

严格因果口径（L-014 / L-064 / L-065）：
- 信号在 T 日收盘后形成，只用 <=T 的数据；T+1 开盘买入（vectorized 近似，
  Phase-2 再用生产因果出场引擎复核净值）。
- 全市场（非选中票口径）先判方向与单调性，避免选择效应伪影（L-064）。
- 逐月符号一致性（>=80% 同号）+ 日度横截面 Spearman IC + 时间切分 OOS。
- 内置 L-061/L-063 已证伪因子（chg20/pos60/vol_ratio）做"阴性对照"，
  若对照因子在本管线里显得很强，说明管线有混杂/bug，结论作废。

只读研究脚本，不写生产、不改任何表。面板缓存到 output/research 以加速迭代。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db.session import SessionLocal

OUT = Path(__file__).resolve().parents[1] / "output" / "research"
CACHE = OUT / "panel_overnight.pkl"

# 流动性 / 可交易硬过滤（全局只保留硬性过滤，符合"策略优先"架构；这里只是研究宇宙）
MIN_AMOUNT_YI = 1.0          # 当日成交额 >= 1 亿（daily_kbars.amount 单位为元，1亿=1e8）
MIN_LIST_DAYS = 60           # 上市满 60 日
TRAIN_END = "2025-06-30"     # 时间切分：<= 训练，> 样本外


def load_panel(force: bool = False) -> pd.DataFrame:
    if CACHE.exists() and not force:
        return pd.read_pickle(CACHE)
    with SessionLocal() as s:
        df = pd.read_sql(
            text(
                """
                SELECT code, trade_date, open, high, low, close,
                       volume, amount, pct_chg, turnover_rate,
                       vol_ratio_5, vol_ratio_20
                FROM daily_kbars
                WHERE trade_date >= '2021-06-01'
                """
            ),
            s.connection(),
        )
        meta = pd.read_sql(
            text("SELECT code, name, industry, is_st, list_date, market FROM stocks"),
            s.connection(),
        )
    df = df.merge(meta, on="code", how="left")
    # 板块：剔除北交所(8/4/920 开头)、剔除 ST
    pref = df["code"].str[:2]
    df["is_bj"] = df["code"].str[0].isin(["4", "8", "9"])
    df["is_st"] = df["is_st"].fillna(0).astype(int)
    df = df.sort_values(["code", "trade_date"]).reset_index(drop=True)
    df.to_pickle(CACHE)
    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("code", group_keys=False)
    prev_close = g["close"].shift(1)

    # 用官方 pct_chg 标记除权除息日：raw 收盘收益与官方 pct 偏离 >1.5pct 视为公司行动，隔夜分量置 NaN
    raw_cc = df["close"] / prev_close - 1
    exdiv = (raw_cc - df["pct_chg"] / 100.0).abs() > 0.015

    eps = 1e-12
    r_on = np.log(df["open"] / (prev_close + eps))           # 隔夜：昨收 -> 今开
    r_id = np.log(df["close"] / (df["open"] + eps))          # 日内：今开 -> 今收
    r_on = r_on.mask(exdiv).replace([np.inf, -np.inf], np.nan)
    r_id = r_id.mask(exdiv).replace([np.inf, -np.inf], np.nan)
    df["r_on"] = r_on
    df["r_id"] = r_id

    # 滚动求和/统计（按股票）
    def roll_sum(col, n):
        return g[col].rolling(n, min_periods=max(5, n // 2)).sum().reset_index(level=0, drop=True)

    def roll_mean(col, n):
        return g[col].rolling(n, min_periods=max(5, n // 2)).mean().reset_index(level=0, drop=True)

    def roll_std(col, n):
        return g[col].rolling(n, min_periods=max(8, n // 2)).std().reset_index(level=0, drop=True)

    for n in (5, 10, 20):
        df[f"on_{n}"] = roll_sum("r_on", n)
        df[f"id_{n}"] = roll_sum("r_id", n)
    df["on_minus_id_10"] = df["on_10"] - df["id_10"]
    df["on_minus_id_20"] = df["on_20"] - df["id_20"]
    # 隔夜跳空方向频率 / 尾部风险（先建 0/1 列再走统一滚动均值，避免慢速 apply）
    df["_gap_up"] = (df["r_on"] > 0).astype(float)
    df["_gap_dn"] = (df["r_on"] < -0.02).astype(float)
    df["gap_up_freq_20"] = roll_mean("_gap_up", 20)
    df["gap_down_tail_20"] = roll_mean("_gap_dn", 20)
    df["on_vol_20"] = roll_std("r_on", 20)
    df["id_vol_20"] = roll_std("r_id", 20)

    # 日内结构
    rng = (df["high"] - df["low"]).clip(lower=eps)
    clv = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng  # -1..1 收位
    df["clv"] = clv
    df["clv_10"] = roll_mean("clv", 10)
    amp = (df["high"] - df["low"]) / df["close"]
    df["amp"] = amp
    df["amp_10"] = g["amp"].rolling(10, min_periods=5).mean().reset_index(level=0, drop=True)
    up_shadow = (df["high"] - df[["open", "close"]].max(axis=1)) / df["close"]
    df["up_shadow"] = up_shadow
    df["up_shadow_10"] = g["up_shadow"].rolling(10, min_periods=5).mean().reset_index(level=0, drop=True)

    # ── 阴性对照（L-061/L-063 已证伪应近零）──
    df["chg20"] = df["close"] / g["close"].shift(20) - 1
    cmin60 = g["low"].rolling(60, min_periods=30).min().reset_index(level=0, drop=True)
    cmax60 = g["high"].rolling(60, min_periods=30).max().reset_index(level=0, drop=True)
    df["pos60"] = (df["close"] - cmin60) / (cmax60 - cmin60 + eps)

    # ── 前瞻收益：T+1 开盘买入 ──
    next_open = g["open"].shift(-1)
    next2_open = g["open"].shift(-2)
    next_close = g["close"].shift(-1)
    close5 = g["close"].shift(-5)
    df["fwd_id_1"] = np.log(next_close / (next_open + eps)).replace([np.inf, -np.inf], np.nan)
    df["fwd_oo_1"] = np.log(next2_open / (next_open + eps)).replace([np.inf, -np.inf], np.nan)
    df["fwd_5d"] = np.log(close5 / (next_open + eps)).replace([np.inf, -np.inf], np.nan)
    # T+1 必须可交易：开盘价有效
    df["entry_ok"] = next_open.gt(0)
    return df


FACTORS = {
    # 未挖的新维度：隔夜/日内分解
    "on_5": "近5日隔夜收益合计",
    "on_10": "近10日隔夜收益合计",
    "on_20": "近20日隔夜收益合计",
    "id_5": "近5日日内收益合计",
    "id_10": "近10日日内收益合计",
    "id_20": "近20日日内收益合计",
    "on_minus_id_10": "10日隔夜-日内差",
    "on_minus_id_20": "20日隔夜-日内差",
    "gap_up_freq_20": "20日向上跳空频率",
    "gap_down_tail_20": "20日大幅低开频率(风险)",
    "on_vol_20": "20日隔夜波动(风险)",
    "id_vol_20": "20日日内波动(风险)",
    "clv_10": "10日平均收盘位置",
    "amp_10": "10日平均振幅(风险)",
    "up_shadow_10": "10日上影线强度",
    # 阴性对照
    "chg20": "[对照]20日动量(应近零)",
    "pos60": "[对照]60日位置(应近零)",
    "vol_ratio_5": "[对照]5日量比(应近零)",
}

FWD = "fwd_5d"


def universe_mask(df: pd.DataFrame) -> pd.Series:
    list_days = (pd.to_datetime(df["trade_date"]) - pd.to_datetime(df["list_date"], errors="coerce")).dt.days
    ok = (
        (df["amount"] >= MIN_AMOUNT_YI * 1e8)  # amount 单位：元(1e8元=1亿)
        & (df["is_st"] == 0)
        & (~df["is_bj"])
        & (list_days >= MIN_LIST_DAYS)
        & df["entry_ok"]
        & df[FWD].notna()
        & df["open"].gt(0)
    )
    return ok


def _spearman_day(group: pd.DataFrame, factor: str) -> float:
    if group[factor].nunique() < 5:
        return np.nan
    return group[factor].corr(group[FWD], method="spearman")


def evaluate(df: pd.DataFrame) -> list[dict]:
    mask = universe_mask(df)
    d = df[mask].copy()
    d["month"] = d["trade_date"].str[:7]
    results = []
    for factor, label in FACTORS.items():
        sub = d.dropna(subset=[factor]).copy()
        if sub.empty:
            continue
        # 日度五分位
        def qbin(s: pd.Series) -> pd.Series:
            try:
                return pd.qcut(s, 5, labels=False, duplicates="drop")
            except Exception:
                return pd.Series(np.nan, index=s.index)
        sub["q"] = sub.groupby("trade_date")[factor].transform(qbin)
        rows = []
        # 每日 Q5-Q1 收益差 & IC
        daily = []
        for date, gp in sub.groupby("trade_date"):
            qm = gp.groupby("q")[FWD].mean()
            if 0 in qm.index and (qm.index.max() >= 4):
                spread = qm.get(4, qm.index.max()) - qm[0]
            else:
                spread = np.nan
            ic = _spearman_day(gp, factor)
            daily.append((date, gp["month"].iloc[0], spread, ic, gp[FWD].mean()))
        dframe = pd.DataFrame(daily, columns=["date", "month", "spread", "ic", "base"])
        # 分月
        monthly = dframe.groupby("month").agg(
            spread=("spread", "mean"), ic=("ic", "mean"), days=("date", "count")
        ).dropna()
        months = monthly[monthly["days"] >= 5]
        if months.empty:
            continue
        # 主方向：全样本 spread 符号
        full_spread = dframe["spread"].mean()
        direction = 1 if full_spread >= 0 else -1
        sign_consistency = float((np.sign(months["spread"]) == direction).mean())
        ic_mean = float(dframe["ic"].mean())
        ic_ttest = float(dframe["ic"].mean() / (dframe["ic"].std() / np.sqrt(dframe["ic"].notna().sum())))
        ic_hitrate = float((dframe["ic"] > 0).mean())
        # 五分位单调性（全样本）
        qmean = sub.groupby("q")[FWD].mean()
        mono = float(np.corrcoef(qmean.index, qmean.values)[0, 1]) if len(qmean) >= 3 else np.nan
        # 时间切分 OOS
        tr = dframe[dframe["date"] <= TRAIN_END]["spread"].mean()
        oo = dframe[dframe["date"] > TRAIN_END]["spread"].mean()
        months_oo = months[months.index > TRAIN_END[:7]]
        sc_oo = float((np.sign(months_oo["spread"]) == direction).mean()) if len(months_oo) else np.nan
        # Q5/Q1 绝对收益（bp，log*1e4）
        q_bp = {int(k): round(float(v) * 1e4, 1) for k, v in qmean.items()}
        results.append({
            "factor": factor, "label": label,
            "n": int(len(sub)), "days": int(dframe["date"].nunique()),
            "direction": direction,
            "spread_bp_5d": round(float(full_spread) * 1e4, 1),
            "month_sign_consistency": round(sign_consistency, 3),
            "months": int(len(months)),
            "ic_mean": round(ic_mean, 4), "ic_t": round(ic_ttest, 2), "ic_hitrate": round(ic_hitrate, 3),
            "quintile_monotonicity": round(mono, 3),
            "train_spread_bp": round(float(tr) * 1e4, 1) if pd.notna(tr) else None,
            "oos_spread_bp": round(float(oo) * 1e4, 1) if pd.notna(oo) else None,
            "oos_month_consistency": round(sc_oo, 3) if pd.notna(sc_oo) else None,
            "quintile_fwd_bp": q_bp,
        })
    return results


def main() -> None:
    global FWD
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--fwd", default=FWD)
    args = ap.parse_args()
    FWD = args.fwd
    print("loading panel ...", flush=True)
    df = load_panel(force=False)
    print(f"panel rows={len(df):,}", flush=True)
    built_cache = OUT / "panel_built.pkl"
    if built_cache.exists() and not args.force:
        import pandas as _pd
        df = _pd.read_pickle(built_cache)
        print(f"loaded built features rows={len(df):,}", flush=True)
    else:
        df = build_features(df)
        df.to_pickle(built_cache)
        print(f"built+cached features rows={len(df):,}", flush=True)
    res = evaluate(df)
    res.sort(key=lambda r: -abs(r["ic_t"]))
    out = {"forward": FWD, "train_end": TRAIN_END, "min_amount_yi": MIN_AMOUNT_YI, "results": res}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"overnight_decomposition_{FWD}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n{'factor':20s} {'dir':>3} {'spread_bp':>9} {'moCons':>6} {'icMean':>7} {'ic_t':>6} {'icHit':>5} {'mono':>5} {'tr_bp':>7} {'oos_bp':>7} {'oosCon':>6}")
    for r in res:
        print(f"{r['factor']:20s} {r['direction']:>3d} {r['spread_bp_5d']:>9.1f} {r['month_sign_consistency']:>6.2f} "
              f"{r['ic_mean']:>7.4f} {r['ic_t']:>6.1f} {r['ic_hitrate']:>5.2f} {r['quintile_monotonicity']:>5.2f} "
              f"{str(r['train_spread_bp']):>7} {str(r['oos_spread_bp']):>7} {str(r['oos_month_consistency']):>6}")
    print("\nsaved:", path)


if __name__ == "__main__":
    main()
