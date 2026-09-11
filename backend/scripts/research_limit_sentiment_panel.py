"""涨停板微观结构情绪面板（市场级，只读研究）。

方向（L-065 合法出路②框架/regime 识别，非已挖尽的个股横截面因子）：
生产 regime 只用「当日上涨比」，情绪只用「涨停率 + 涨停/跌停数量比」(aggregator._market_base/
_market_sentiment)，从未使用涨停板微观结构——炸板率（触板封不住=亏钱效应/分歧）、连板高度、
连板梯队。本模块从全市场 daily_kbars 的 OHLC/pct_chg **确定性重建**这些日级市场情绪序列，
并用官方 limit_up_board（仅近 6 个月）在重叠期校验重建精度。

全部特征在信号日 T 收盘后可得，T+1 开盘入场前已知 → 无未来函数。

重建规则（版本 v1，确定性、可复现）：
- 板块涨跌停幅度按代码前缀 + ST：主板10%、创业板(300/301)/科创板(688/689) 20%、
  北交所(8/4/920) 30%、ST 5%。
- 收盘涨停 U：pct_chg >= lim - TOL 且 pct_chg <= lim + 0.6（挡住新股无涨跌幅）。
- 收盘跌停 D：pct_chg <= -lim + TOL。
- 触板：日内高幅 hi_ret=(high/preclose-1)*100 >= lim - TOL。
- 炸板 Z：触板但未封板（pct_chg <= lim - 1.0），且非跌停。
- 连板：逐 code 按交易日排序，收盘涨停连续天数。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db.session import SessionLocal  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "output" / "research"
OUT.mkdir(parents=True, exist_ok=True)
PANEL = OUT / "limit_sentiment_panel.pkl"

TOL = 0.35  # 收盘涨停 pct 容差（百分点，仅用于新股无涨跌幅护栏）


def board_limit_pct(code: str, is_st: bool) -> float:
    if is_st:
        return 5.0
    c = str(code)
    if c.startswith(("300", "301", "688", "689")):
        return 20.0
    if c.startswith(("8", "4", "920")):
        return 30.0
    return 10.0


def load_raw(start: str = "2025-01-01") -> pd.DataFrame:
    stmt = text(
        """
        SELECT k.code, k.trade_date, k.open, k.high, k.low, k.close, k.pct_chg,
               s.name, s.market
        FROM daily_kbars k
        LEFT JOIN stocks s ON s.code = k.code
        WHERE k.trade_date >= :start
          AND k.close > 0 AND k.high > 0 AND k.low > 0
        """
    )
    with SessionLocal() as session:
        df = pd.DataFrame(session.execute(stmt, {"start": start}).mappings().all())
    return df


def build_daily_flags(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    name = df["name"].fillna("")
    is_st = name.str.contains("ST", case=False, na=False)
    # 与生产可交易宇宙对齐：剔除北交所（unsupported_market）与 ST（硬性不可交易）
    is_bj = df["code"].str.startswith(("8", "4", "920"))
    lim = [board_limit_pct(c, st) for c, st in zip(df["code"], is_st)]
    df["lim"] = lim
    df["tradable_univ"] = ~is_bj & ~is_st
    pct = df["pct_chg"].astype(float)
    preclose = df["close"].astype(float) / (1 + pct / 100.0)
    # 精确涨停价/跌停价（四舍五入到分），用 high/close 是否触及该价格判定，避免容差误判
    up_lim_px = preclose * (1 + df["lim"] / 100.0)
    up_lim_px = (up_lim_px * 100).round() / 100.0
    dn_lim_px = preclose * (1 - df["lim"] / 100.0)
    dn_lim_px = (dn_lim_px * 100).round() / 100.0
    hi = df["high"].astype(float)
    cl = df["close"].astype(float)
    lo = df["low"].astype(float)
    not_new = pct <= df["lim"] + TOL  # 挡住上市前五日无涨跌幅限制（涨幅远超板块限）
    # 收盘涨停 / 跌停（收盘价 >= 涨停价，1 厘容差）
    sealed_up = (cl >= up_lim_px - 0.001) & not_new
    sealed_dn = (cl <= dn_lim_px + 0.001) & not_new
    touched_up = (hi >= up_lim_px - 0.001) & not_new  # 日内最高价触及涨停价
    # 炸板：盘中触板但收盘未封住，且不是跌停
    zha = touched_up & (cl < up_lim_px - 0.001) & ~sealed_dn
    # 严格炸板代理：触板且仅从涨停价回落 <3pct（更像"封死后尾盘炸开"，区别于摸板未封）
    zha_strict = zha & (pct >= df["lim"] - 3.0)
    _ = (lo,)  # 预留
    df["sealed_up"] = sealed_up & df["tradable_univ"]
    df["sealed_dn"] = sealed_dn & df["tradable_univ"]
    df["zha"] = zha & df["tradable_univ"]
    df["zha_strict"] = zha_strict & df["tradable_univ"]
    # 连板 streak（仅收盘涨停）
    df = df.sort_values(["code", "trade_date"]).reset_index(drop=True)
    grp = df.groupby("code", sort=False)
    df["prev_up"] = grp["sealed_up"].shift(1).fillna(False)
    # 交易日连续性由「前一行同 code 是否涨停」近似（停牌会自然断 streak：前一行非涨停即断）
    streak = np.zeros(len(df), dtype=np.int16)
    codes = df["code"].to_numpy()
    sup = df["sealed_up"].to_numpy()
    prev = df["prev_up"].to_numpy()
    last_code = None
    cur = 0
    for i in range(len(df)):
        if codes[i] != last_code:
            cur = 0
            last_code = codes[i]
        if sup[i]:
            cur = cur + 1 if prev[i] else 1
        else:
            cur = 0
        streak[i] = cur
    df["streak"] = streak
    return df


def aggregate_market(df: pd.DataFrame) -> pd.DataFrame:
    g = df[df.tradable_univ].groupby("trade_date")
    daily = pd.DataFrame({
        "lu_n": g["sealed_up"].sum(),
        "ld_n": g["sealed_dn"].sum(),
        "zha_n": g["zha"].sum(),
        "zha_strict_n": g["zha_strict"].sum(),
        "univ_n": g["code"].nunique(),
    })
    # 连板高度 / 梯队
    up = df[df.sealed_up]
    h = up.groupby("trade_date")["streak"].agg(max_height="max",
                                               ladder2=lambda s: int((s >= 2).sum()),
                                               ladder3=lambda s: int((s >= 3).sum()),
                                               ladder4=lambda s: int((s >= 4).sum()))
    daily = daily.join(h, how="left").fillna({"max_height": 0, "ladder2": 0, "ladder3": 0, "ladder4": 0})
    daily["touch_n"] = daily["lu_n"] + daily["zha_n"]
    daily["broken_rate"] = daily["zha_n"] / daily["touch_n"].replace(0, np.nan)
    daily["broken_rate_strict"] = daily["zha_strict_n"] / (daily["lu_n"] + daily["zha_strict_n"]).replace(0, np.nan)
    daily["seal_rate"] = daily["lu_n"] / daily["touch_n"].replace(0, np.nan)
    daily["lu_ld_ratio"] = daily["lu_n"] / daily["ld_n"].replace(0, np.nan)
    daily = daily.sort_index()
    # 一阶变化与多日平滑（L-003：单日脉冲会误导，用平滑/变化方向）
    for col in ["broken_rate", "broken_rate_strict", "max_height", "lu_n", "ladder2", "seal_rate"]:
        daily[f"d_{col}"] = daily[col].diff()
        daily[f"ma3_{col}"] = daily[col].rolling(3, min_periods=1).mean()
    daily["broken_rate_ma3"] = daily["ma3_broken_rate"]
    daily["broken_rate_strict_ma3"] = daily["ma3_broken_rate_strict"]
    daily["seal_rate_ma3"] = daily["ma3_seal_rate"]
    daily["height_ma3"] = daily["ma3_max_height"]
    daily = daily.reset_index()
    return daily


def validate_against_official(daily: pd.DataFrame) -> pd.DataFrame:
    """与官方 limit_up_board 在重叠日比较 U/Z 计数。"""
    with SessionLocal() as session:
        off = pd.DataFrame(session.execute(text(
            """SELECT l.trade_date,
                      SUM(l.limit_type='U') u,
                      SUM(l.limit_type='D') d,
                      SUM(l.limit_type='Z') z
               FROM limit_up_board l
               LEFT JOIN stocks s ON s.code = l.code
               WHERE l.trade_date>='2026-03-01'
                 AND s.name NOT LIKE '%ST%'
                 AND l.code NOT LIKE '8%' AND l.code NOT LIKE '4%' AND l.code NOT LIKE '920%'
               GROUP BY l.trade_date""")).mappings().all())
    if off.empty:
        return off
    m = daily.merge(off, on="trade_date", how="inner")
    m["u_err"] = m["lu_n"] - m["u"].astype(float)
    m["z_err"] = m["zha_n"] - m["z"].astype(float)
    return m


def main() -> None:
    raw = load_raw()
    print("raw rows:", len(raw), "dates:", raw.trade_date.min(), raw.trade_date.max())
    flags = build_daily_flags(raw)
    daily = aggregate_market(flags)
    daily.to_pickle(PANEL)
    print("daily panel:", daily.shape, "->", PANEL)
    print(daily.tail(8).to_string())
    val = validate_against_official(daily)
    if not val.empty:
        # 官方表近期同步不全，只在官方涨停家数看起来完整（>=30）的日子评估
        ok = val[val["u"].astype(float) >= 30]
        print("\n=== 重建 vs 官方（重叠期，官方U>=30 的", len(ok), "天）===")
        if len(ok):
            print("U count: recon mean %.1f official mean %.1f, MAE %.2f, corr %.3f"
                  % (ok.lu_n.mean(), ok["u"].astype(float).mean(),
                     ok.u_err.abs().mean(), ok[["lu_n", "u"]].corr().iloc[0, 1]))
            print("Z count: recon mean %.1f official mean %.1f, MAE %.2f, corr %.3f"
                  % (ok.zha_n.mean(), ok["z"].astype(float).mean(),
                     ok.z_err.abs().mean(), ok[["zha_n", "z"]].corr().iloc[0, 1]))
            # 炸板率一致性
            obr = ok["z"].astype(float) / (ok["u"].astype(float) + ok["z"].astype(float))
            print("broken_rate: recon mean %.3f official mean %.3f, corr %.3f, mean abs diff %.3f"
                  % (ok.broken_rate.mean(), obr.mean(), ok.broken_rate.corr(obr),
                     (ok.broken_rate - obr).abs().mean()))
        val.to_csv(OUT / "limit_sentiment_official_validation.csv", index=False)
        print("validation detail saved.")


if __name__ == "__main__":
    main()
