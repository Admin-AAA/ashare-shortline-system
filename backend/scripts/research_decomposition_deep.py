"""Phase-1b：隔夜/日内分解因子的中性化 + regime 条件化 + 持有期深度检验。

在 research_overnight_decomposition 的全市场初筛基础上，回答三个问题：
1) 日内反转(id_n)/隔夜-日内差 是否只是规模/换手混杂？做 size 分层 + 换手中性。
2) 因子符号是否随市场 regime 翻转（L-060）？按横截面等权市场状态分 regime，
   检验每个 regime 内的方向、单调性与逐月同号率。
3) edge 落在哪个持有期（买入当日日内 / 持有1日 / 5日）。

只读。读 output/research/panel_built.pkl（由 phase1 生成）。
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sqlalchemy import text
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db.session import SessionLocal

OUT = Path(__file__).resolve().parents[1] / "output" / "research"
BUILT = OUT / "panel_built.pkl"

FACTORS = ["id_20", "on_minus_id_20", "id_10", "on_20", "id_5", "on_10",
           "chg20", "pos60", "vol_ratio_5", "gap_down_tail_20", "on_vol_20", "id_vol_20"]
FWDS = ["fwd_id_1", "fwd_oo_1", "fwd_5d"]


def load():
    df = pd.read_pickle(BUILT)
    with SessionLocal() as s:
        db = pd.read_sql(text("SELECT code, trade_date, circ_mv, total_mv, turnover_rate AS to_db FROM daily_basic"), s.connection())
    df = df.merge(db, on=["code", "trade_date"], how="left")
    return df


def market_regime(df: pd.DataFrame) -> pd.DataFrame:
    """用横截面等权市场组合构造 regime（自包含，不依赖指数长度）。"""
    eps = 1e-12
    prev_close = df.sort_values(["code", "trade_date"]).groupby("code")["close"].shift(1)
    rcc = np.log(df["close"] / (prev_close + eps)).replace([np.inf, -np.inf], np.nan)
    tmp = pd.DataFrame({"trade_date": df["trade_date"], "rcc": rcc})
    mkt = tmp.groupby("trade_date")["rcc"].mean().sort_index()
    idx = mkt.cumsum()
    ma20 = idx.rolling(20, min_periods=10).mean()
    ma60 = idx.rolling(60, min_periods=30).mean()
    mom20 = idx.diff(20)
    regime = pd.Series("震荡", index=idx.index)
    regime[(idx > ma60) & (ma20 > ma60) & (mom20 > 0)] = "强"
    regime[(idx < ma60) & (mom20 < 0)] = "弱"
    out = pd.DataFrame({"regime": regime, "mkt_ret": mkt}, index=idx.index).reset_index()
    return out


def _daily_table(df: pd.DataFrame, factor: str, fwd: str) -> pd.DataFrame:
    """返回每日：top/bottom 五分位均值、多空价差、spearman IC、样本数。"""
    d = df.dropna(subset=[factor, fwd]).copy()
    if d.empty:
        return pd.DataFrame()
    d["fr"] = d.groupby("trade_date")[factor].rank(pct=True)
    d["frr"] = d.groupby("trade_date")[factor].rank()
    d["yr"] = d.groupby("trade_date")[fwd].rank()
    top = d[d["fr"] >= 0.8].groupby("trade_date")[fwd].mean()
    bot = d[d["fr"] <= 0.2].groupby("trade_date")[fwd].mean()
    n = d.groupby("trade_date")[fwd].size()
    # 向量化日内 spearman
    def ic(g):
        if len(g) < 10:
            return np.nan
        return g["frr"].corr(g["yr"])
    ic = d.groupby("trade_date").apply(ic)
    t = pd.DataFrame({"top": top, "bot": bot, "n": n, "ic": ic})
    t["spread"] = t["top"] - t["bot"]
    t["month"] = t.index.str[:7]
    return t


def summarize(t: pd.DataFrame, direction: int | None = None) -> dict:
    t = t.dropna(subset=["spread"])
    if len(t) == 0:
        return {}
    if direction is None:
        direction = 1 if t["spread"].mean() >= 0 else -1
    months = t.groupby("month")["spread"].mean()
    months = months[t.groupby("month").size().reindex(months.index).fillna(0) >= 5] if False else months
    return {
        "days": int(len(t)),
        "spread_bp": round(float(t["spread"].mean()) * 1e4, 1),
        "top_bp": round(float(t["top"].mean()) * 1e4, 1),
        "bot_bp": round(float(t["bot"].mean()) * 1e4, 1),
        "ic_mean": round(float(t["ic"].mean()), 4),
        "ic_t": round(float(t["ic"].mean() / (t["ic"].std() / np.sqrt(t["ic"].notna().sum()))), 1),
        "month_consistency": round(float((np.sign(months) == direction).mean()), 3),
        "months": int(len(months)),
        "direction": direction,
    }


def size_stratified(df, factor, fwd) -> dict:
    """每日按流通市值分 3 层，层内再算因子多空价差，等权平均——非参数控制规模。"""
    d = df.dropna(subset=[factor, fwd, "circ_mv"]).copy()
    d["size_bucket"] = d.groupby("trade_date")["circ_mv"].transform(lambda s: pd.qcut(s, 3, labels=False, duplicates="drop"))
    pieces = []
    for sb, g in d.groupby("size_bucket"):
        tt = _daily_table(g, factor, fwd)
        tt["size_bucket"] = sb
        pieces.append(tt)
    all_t = pd.concat(pieces)
    # 每日跨规模层平均价差
    daily = all_t.groupby(level=0)["spread"].mean()
    daily_ic = all_t.groupby(level=0)["ic"].mean()
    t = pd.DataFrame({"spread": daily, "ic": daily_ic})
    t["month"] = t.index.str[:7]
    direction = 1 if t["spread"].mean() >= 0 else -1
    m = t.groupby("month")["spread"].mean()
    return {
        "days": int(len(t)),
        "spread_bp": round(float(t["spread"].mean()) * 1e4, 1),
        "ic_mean": round(float(t["ic"].mean()), 4),
        "month_consistency": round(float((np.sign(m) == direction).mean()), 3),
        "direction": direction,
    }


def main():
    df = load()
    print(f"loaded {len(df):,}; with circ_mv: {df['circ_mv'].notna().sum():,}")
    reg = market_regime(df)
    df = df.merge(reg, on="trade_date", how="left")
    # 研究宇宙（与 phase1 一致的硬过滤）
    list_days = (pd.to_datetime(df["trade_date"]) - pd.to_datetime(df["list_date"], errors="coerce")).dt.days
    uni = ((df["amount"] >= 1e8) & (df["is_st"] == 0) & (~df["is_bj"]) &
           (list_days >= 60) & df["entry_ok"])
    df = df[uni].copy()
    print("regime day counts:", df.groupby(["trade_date", "regime"]).size().groupby("regime").size().to_dict())

    report = {"horizons": {}, "regime_conditional_fwd5d": {}, "size_stratified_fwd5d": {}}

    # 1) 多持有期（全市场）
    for fwd in FWDS:
        rows = {}
        for fac in FACTORS:
            t = _daily_table(df, fac, fwd)
            rows[fac] = summarize(t)
        report["horizons"][fwd] = rows
        print(f"\n===== 持有期 {fwd}（全市场） =====")
        print(f"{'factor':18s} {'spread_bp':>9} {'ic':>7} {'ic_t':>6} {'moCons':>6} {'top_bp':>8} {'bot_bp':>8}")
        for fac in FACTORS:
            r = rows[fac]
            if r:
                print(f"{fac:18s} {r['spread_bp']:>9.1f} {r['ic_mean']:>7.4f} {r['ic_t']:>6.1f} {r['month_consistency']:>6.2f} {r['top_bp']:>8.1f} {r['bot_bp']:>8.1f}")

    # 2) regime 条件化（fwd_5d）
    print("\n\n===== regime 条件化（fwd_5d） =====")
    for fac in ["id_20", "on_minus_id_20", "id_10", "chg20", "gap_down_tail_20", "id_vol_20"]:
        report["regime_conditional_fwd5d"][fac] = {}
        print(f"\n-- {fac} --")
        for rg in ["强", "震荡", "弱"]:
            t = _daily_table(df[df["regime"] == rg], fac, "fwd_5d")
            r = summarize(t)
            report["regime_conditional_fwd5d"][fac][rg] = r
            if r:
                print(f"  regime={rg} days={r['days']:>3} spread={r['spread_bp']:>7.1f}bp ic={r['ic_mean']:>7.4f} t={r['ic_t']:>5.1f} moCons={r['month_consistency']:.2f} (top{r['top_bp']}/bot{r['bot_bp']})")

    # 3) 规模分层（fwd_5d，仅 2025-01 起 daily_basic 覆盖）
    print("\n\n===== 流通市值分层后（fwd_5d，控制规模） =====")
    for fac in ["id_20", "on_minus_id_20", "id_10", "chg20", "pos60", "gap_down_tail_20", "id_vol_20"]:
        r = size_stratified(df, fac, "fwd_5d")
        report["size_stratified_fwd5d"][fac] = r
        print(f"  {fac:18s} spread={r.get('spread_bp')}bp ic={r.get('ic_mean')} moCons={r.get('month_consistency')} days={r.get('days')}")

    p = OUT / "decomposition_deep.json"
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("\nsaved:", p)


if __name__ == "__main__":
    main()
