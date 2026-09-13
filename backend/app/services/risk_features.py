"""横截面风险特征：个股特质波动 ivol20 / 特质下行半波动 ivol_dn20 及其当日全市场分位（L-093/L-099）。

定义（严格 T 收盘可得、无前视）：
- 个股日收益 r_t = pct_chg/100；市场收益 m_t = 中证500(000905.SH) pct_chg/100，按交易日对齐。
- 20 个交易日滚动：beta_t = Cov(r,m)_20 / Var(m)_20；残差 e_t = r_t - beta_t * m_t；
  ivol20_t    = Std(e)_{20}（min_periods=16）；
  ivol_dn20_t = sqrt(mean(min(e_t,0)^2))_{20}（min_periods=16，下行半标准差）。
- 当日全市场横截面分位 pct = rank(值)/N ∈ (0,1]，供 regime 风险否决使用。

为什么用特质波动而非总波动：总波动混入市场 beta（L-080 证明 beta/指数趋势是会翻号的
regime 量）；剔除市场后的个股特质风险在 v2 选中池三窗口（冻结/前瞻/run3）同向为负，
是 L-061~L-083 未构造过的衍生因子（非 daily_kbars 存量列）。详见 docs/lessons L-093。

为什么 ivol_dn20 与 ivol20 并存且共享同一残差定义（L-099）：生产出场是非对称的
（+6% 止盈截断赢家 / -4.5% 止损 + 隔夜跳空放大输家），驱动大亏的是**左尾**。实测在
v3 选中池里，低 ivol_dn20（分位<=0.25）在**弱势** regime 三窗口 Δcohort 全正、被砍卡三窗
均收益 -0.9~-1.4%，而低 ivol20（双侧）无效——说明该毒格是"下行安静票"而非"低波动票"。
两条母列必须使用同一 beta/残差口径，否则不可比较。详见 docs/lessons L-099。

**注意**：`compute_ivol20` 的 `.std()` 是 L-093 已落库（201.6 万行）的历史公式，
不得改写；新增的 `compute_ivol_dn20` 走半标准差公式，两者共用 `_resid_series`。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

IVOL_WINDOW = 20
IVOL_MIN_PERIODS = 16
BENCHMARK_TS_CODE = "000905.SH"


def _resid_series(
    returns: pd.Series,
    market_returns: pd.Series,
    window: int = IVOL_WINDOW,
    min_periods: int = IVOL_MIN_PERIODS,
) -> pd.Series:
    """20 日滚动回归残差 e = r - beta*m（ivol20 / ivol_dn20 共用的唯一残差口径）。"""
    r = pd.to_numeric(returns, errors="coerce").astype(float)
    m = pd.to_numeric(market_returns, errors="coerce").astype(float)
    cov = r.rolling(window, min_periods=min_periods).cov(m)
    mvar = m.rolling(window, min_periods=min_periods).var()
    beta = cov / mvar.where(mvar > 0)
    return r - beta * m


def compute_ivol20(
    returns: pd.Series,
    market_returns: pd.Series,
    window: int = IVOL_WINDOW,
    min_periods: int = IVOL_MIN_PERIODS,
) -> pd.Series:
    """对齐索引后逐点滚动回归，返回残差标准差序列（与 returns 同索引）。

    returns / market_returns 需已按相同交易日索引对齐（调用方负责 merge/ffill 口径）。
    """
    resid = _resid_series(returns, market_returns, window, min_periods)
    return resid.rolling(window, min_periods=min_periods).std()


def compute_ivol_dn20(
    returns: pd.Series,
    market_returns: pd.Series,
    window: int = IVOL_WINDOW,
    min_periods: int = IVOL_MIN_PERIODS,
) -> pd.Series:
    """同一残差序列的**下行半标准差** sqrt(mean(min(e,0)^2))（与 returns 同索引）。

    与 compute_ivol20 共用 _resid_series，仅把"双侧标准差"换成"只计左尾的二阶矩"。
    全窗残差均 >= 0 时为 0（完全只涨不跌的特质路径），是合法值而非缺失。
    """
    resid = _resid_series(returns, market_returns, window, min_periods)
    downside_sq = resid.clip(upper=0.0) ** 2
    return np.sqrt(downside_sq.rolling(window, min_periods=min_periods).mean())


def ivol_pct_series(values: pd.Series) -> pd.Series:
    """单日全市场横截面分位（pct rank），缺失不参与排名。"""
    return values.rank(pct=True, method="average")


def _aligned_stock_blocks(
    kbars: pd.DataFrame,
    index_rows: pd.DataFrame,
    code_col: str,
    date_col: str,
):
    """把日线面板与中证500按交易日对齐后逐股切块，yield (code, dates, r, m)。

    内存安全：只对单只股票持有 Series，不构造全市场宽表。
    """
    idx = index_rows[[date_col, "pct_chg"]].copy()
    idx["mkt"] = pd.to_numeric(idx["pct_chg"], errors="coerce").astype(float) / 100.0
    df = kbars[[code_col, date_col, "pct_chg"]].copy()
    df["r"] = pd.to_numeric(df["pct_chg"], errors="coerce").astype(float) / 100.0
    df = df.merge(idx[[date_col, "mkt"]], on=date_col, how="left")
    df = df.sort_values([code_col, date_col])
    for code, blk in df.groupby(code_col, sort=False):
        yield code, blk[date_col].values, blk["r"], blk["mkt"]


def build_ivol_panel(
    kbars: pd.DataFrame,
    index_rows: pd.DataFrame,
    *,
    code_col: str = "code",
    date_col: str = "trade_date",
) -> pd.DataFrame:
    """给一张含 code/trade_date/pct_chg 的日线面板计算 ivol20（逐股滚动，内存安全）。

    index_rows 需含 trade_date 与 pct_chg（中证500）。返回 [code, trade_date, ivol20]。
    """
    parts = []
    for code, dates, r, m in _aligned_stock_blocks(kbars, index_rows, code_col, date_col):
        iv = compute_ivol20(r, m)
        parts.append(pd.DataFrame({
            code_col: code,
            date_col: dates,
            "ivol20": iv.astype(float).values,
        }))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
        columns=[code_col, date_col, "ivol20"])


def build_ivol_dn20_panel(
    kbars: pd.DataFrame,
    index_rows: pd.DataFrame,
    *,
    code_col: str = "code",
    date_col: str = "trade_date",
) -> pd.DataFrame:
    """同 build_ivol_panel，但计算下行半波动 ivol_dn20。返回 [code, trade_date, ivol_dn20]。"""
    parts = []
    for code, dates, r, m in _aligned_stock_blocks(kbars, index_rows, code_col, date_col):
        iv = compute_ivol_dn20(r, m)
        parts.append(pd.DataFrame({
            code_col: code,
            date_col: dates,
            "ivol_dn20": iv.astype(float).values,
        }))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
        columns=[code_col, date_col, "ivol_dn20"])


def attach_ivol20_pct(
    cards: list[dict[str, Any]],
    trade_date: str,
    pct_map: Mapping[tuple[str, str], float],
    *,
    field: str = "ivol20_pct",
) -> None:
    """就地为卡片附加当日全市场 ivol20 分位；查不到（如停牌/新股/北交所未参与排名）留空。"""
    for card in cards or []:
        code = str(card.get("code") or "")
        if not code:
            continue
        pct = pct_map.get((str(trade_date), code))
        if pct is not None and not (isinstance(pct, float) and np.isnan(pct)):
            card[field] = float(pct)


def load_ivol20_pct_map(session, trade_dates: Iterable[str]) -> dict[tuple[str, str], float]:
    """从 daily_kbars.ivol20 读取给定交易日的全市场特质波动并按日横截面排名。

    返回 {(trade_date, code): pct∈(0,1]}。与生产可交易宇宙一致：剔北交所（8/4/920 开头）。
    结果进程内可缓存；ivol20 为 T 收盘写入的历史列，只读、无前视。
    """
    from sqlalchemy import text as _text

    dates = sorted({str(d) for d in trade_dates})
    if not dates:
        return {}
    stmt = _text(
        "SELECT trade_date, code, ivol20 FROM daily_kbars "
        "WHERE trade_date BETWEEN :d0 AND :d1 AND ivol20 IS NOT NULL AND ivol20 > 0 "
        "AND code NOT LIKE '8%' AND code NOT LIKE '4%' AND code NOT LIKE '920%'"
    )
    rows = session.execute(stmt, {"d0": dates[0], "d1": dates[-1]}).mappings().all()
    if not rows:
        return {}
    df = pd.DataFrame(rows)
    df["pct"] = df.groupby("trade_date")["ivol20"].rank(pct=True, method="average")
    return {(str(r.trade_date), str(r.code)): float(r.pct) for r in df.itertuples()}


def attach_ivol_dn20_pct(
    cards: list[dict[str, Any]],
    trade_date: str,
    pct_map: Mapping[tuple[str, str], float],
    *,
    field: str = "ivol_dn20_pct",
) -> None:
    """就地为卡片附加当日全市场 ivol_dn20 分位；查不到（如停牌/新股/北交所未参与排名）留空。"""
    attach_ivol20_pct(cards, trade_date, pct_map, field=field)


# 允许做全市场横截面排名的持久化列白名单（防 SQL 拼接注入；新增列须显式登记）。
_PCT_RANK_COLUMNS = frozenset({"ivol20", "ivol_dn20"})


def _load_pct_map(
    session,
    trade_dates: Iterable[str],
    column: str,
    *,
    positive_only: bool,
) -> dict[tuple[str, str], float]:
    """从 daily_kbars 指定列读取指定交易日的全市场横截面分位。

    返回 {(trade_date, code): pct∈(0,1]}。与生产可交易宇宙一致：剔北交所（8/4/920 开头）。
    列值为 T 收盘写入的历史值，只读、无前视。positive_only 用于剔除无意义的 0（如 ivol20
    全窗残差恒零的停牌/一字板路径）；ivol_dn20 的 0 是合法值（全窗残差均为正），不剔除。
    """
    from sqlalchemy import text as _text

    if column not in _PCT_RANK_COLUMNS:
        raise ValueError(f"unsupported risk percentile column: {column}")
    dates = sorted({str(d) for d in trade_dates})
    if not dates:
        return {}
    zero_filter = f"AND {column} > 0 " if positive_only else ""
    stmt = _text(
        f"SELECT trade_date, code, {column} AS v FROM daily_kbars "
        f"WHERE trade_date BETWEEN :d0 AND :d1 AND {column} IS NOT NULL "
        f"{zero_filter}"
        "AND code NOT LIKE '8%' AND code NOT LIKE '4%' AND code NOT LIKE '920%'"
    )
    rows = session.execute(stmt, {"d0": dates[0], "d1": dates[-1]}).mappings().all()
    if not rows:
        return {}
    df = pd.DataFrame(rows)
    df["pct"] = df.groupby("trade_date")["v"].rank(pct=True, method="average")
    return {(str(r.trade_date), str(r.code)): float(r.pct) for r in df.itertuples()}


def load_ivol20_pct_map(session, trade_dates: Iterable[str]) -> dict[tuple[str, str], float]:
    """从 daily_kbars.ivol20 读取给定交易日的全市场特质波动并按日横截面排名。"""
    return _load_pct_map(session, trade_dates, "ivol20", positive_only=True)


def load_ivol_dn20_pct_map(session, trade_dates: Iterable[str]) -> dict[tuple[str, str], float]:
    """从 daily_kbars.ivol_dn20 读取给定交易日的全市场特质下行半波动并按日横截面排名。"""
    return _load_pct_map(session, trade_dates, "ivol_dn20", positive_only=False)


__all__ = [
    "IVOL_WINDOW",
    "IVOL_MIN_PERIODS",
    "BENCHMARK_TS_CODE",
    "compute_ivol20",
    "compute_ivol_dn20",
    "ivol_pct_series",
    "build_ivol_panel",
    "build_ivol_dn20_panel",
    "attach_ivol20_pct",
    "attach_ivol_dn20_pct",
    "load_ivol20_pct_map",
    "load_ivol_dn20_pct_map",
]
